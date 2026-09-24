"""SSM Run Command dispatch. The Lambda role can only SendCommand to:
  - ITOps-* documents (catalog actions, parameters constrained by SSM allowedValues/allowedPattern)
  - AWS-RunPatchBaseline
  - AWS-RunPowerShellScript / AWS-RunShellScript (human-approved generated scripts only)
and only against managed instances tagged ITOps:Managed=true (enforced in IAM).
"""
import os

import boto3

_ssm = boto3.client("ssm")
DELIVERY_TIMEOUT = int(os.environ.get("SSM_DELIVERY_TIMEOUT", "3600"))  # offline laptops wait up to 1h
OUTPUT_LIMIT = 4000

TERMINAL = {"Success", "Failed", "TimedOut", "Cancelled", "Undeliverable", "Terminated",
            "DeliveryTimedOut", "ExecutionTimedOut", "InvalidPlatform", "AccessDenied"}
PLATFORMS = {"Windows": "windows", "Linux": "linux", "MacOS": "macos"}


def _comment(ticket_id):
    return f"ITOps {ticket_id}"[:100]


def send_action(action, instance_ids, params, ticket_id):
    parameters = {k: [v] for k, v in params.items()}
    for k, v in action.get("fixed_parameters", {}).items():
        parameters[k] = v if isinstance(v, list) else [v]
    kwargs = {
        "InstanceIds": instance_ids,
        "DocumentName": action["document"],
        "Parameters": parameters,
        "Comment": _comment(ticket_id),
        "TimeoutSeconds": DELIVERY_TIMEOUT,
    }
    if not action.get("managed_document"):
        kwargs["DocumentVersion"] = "$LATEST"
    return _ssm.send_command(**kwargs)["Command"]["CommandId"]


def send_script(platform, script, instance_ids, ticket_id, timeout=600):
    document = "AWS-RunPowerShellScript" if platform == "windows" else "AWS-RunShellScript"
    return _ssm.send_command(
        InstanceIds=instance_ids,
        DocumentName=document,
        Parameters={"commands": script.split("\n"), "executionTimeout": [str(timeout)]},
        Comment=_comment(ticket_id),
        TimeoutSeconds=DELIVERY_TIMEOUT,
    )["Command"]["CommandId"]


def _ran(plugin):
    # Multi-platform documents report the other OSes' steps as "Success" with a
    # "Step execution skipped due to unsatisfied preconditions" message.
    return "execution skipped" not in (plugin.get("Output") or "").lower()


def get_invocation(command_id, instance_id):
    """Status plus stdout/stderr of the step(s) that actually ran.
    GetCommandInvocation without PluginName returns no output for multi-step documents."""
    listing = _ssm.list_command_invocations(CommandId=command_id, InstanceId=instance_id, Details=True)
    if not listing["CommandInvocations"]:
        return None
    inv = listing["CommandInvocations"][0]
    plugins = [p for p in inv.get("CommandPlugins", []) if _ran(p)] or inv.get("CommandPlugins", [])
    stdout, stderr, code = [], [], None
    for p in plugins:
        try:
            r = _ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id, PluginName=p["Name"])
        except _ssm.exceptions.InvocationDoesNotExist:
            continue
        stdout.append(r.get("StandardOutputContent") or "")
        stderr.append(r.get("StandardErrorContent") or "")
        if code in (None, 0):
            code = r.get("ResponseCode")
    return {
        "status": inv["Status"],
        "status_details": inv.get("StatusDetails"),
        "response_code": code,
        "steps": [p["Name"] for p in plugins],
        "stdout": "\n".join(s for s in stdout if s)[-OUTPUT_LIMIT:],
        "stderr": "\n".join(s for s in stderr if s)[-OUTPUT_LIMIT // 2:],
    }
