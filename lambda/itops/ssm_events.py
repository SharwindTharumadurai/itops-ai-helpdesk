"""EventBridge consumer:
  - aws.ssm "EC2 Command Invocation Status-change Notification" -> record output, close ticket
  - scheduled rule (every 6h)                                     -> sync device inventory
"""
import json
import logging
import re

import boto3

import dispatcher
import store

log = logging.getLogger()
log.setLevel(logging.INFO)

_ssm = boto3.client("ssm")
MANAGED_TAG, OWNER_TAG, ROLE_TAG = "ITOps:Managed", "ITOps:Owner", "ITOps:Role"
_REDACT = re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b(\s*[:=]\s*)\S+")


class RetryLater(Exception):
    """Raised so EventBridge's async Lambda retry re-delivers the event later."""


def handler(event, context):
    if event.get("source") == "aws.ssm":
        return _on_command_status(event["detail"])
    if event.get("detail-type") == "Scheduled Event" or event.get("action") == "sync_inventory":
        return _sync_inventory()
    log.warning("Ignored event: %s", json.dumps(event)[:500])


def _redact(text):
    return _REDACT.sub(r"\1\2[REDACTED]", text or "")


def _on_command_status(detail):
    command_id, instance_id, status = detail.get("command-id"), detail.get("instance-id"), detail.get("status")
    if status not in dispatcher.TERMINAL:
        return
    ticket_id = store.get_command_map(command_id)
    if not ticket_id:
        # Not ours (someone ran a command from the console), or the API Lambda hasn't
        # written the mapping yet. Only the latter is worth a retry.
        comment = _ssm.list_commands(CommandId=command_id)["Commands"][0].get("Comment", "")
        if comment.startswith("ITOps "):
            raise RetryLater(f"Mapping for {command_id} not written yet")
        return

    inv = dispatcher.get_invocation(command_id, instance_id) or {"status": status}
    inv["stdout"], inv["stderr"] = _redact(inv.get("stdout")), _redact(inv.get("stderr"))
    store.put_execution(ticket_id, command_id, instance_id, inv)
    final = "SUCCESS" if inv["status"] == "Success" else "FAILED"
    try:
        store.transition(ticket_id, ("DISPATCHED", "APPROVED"), final, "ssm",
                         note=f"{inv['status']} (exit {inv.get('response_code')})")
    except store.Conflict:
        log.info("Ticket %s already closed", ticket_id)
    log.info(json.dumps({"audit": "command_finished", "ticket": ticket_id, "command_id": command_id,
                         "instance": instance_id, "status": inv["status"]}))


def _sync_inventory():
    count = 0
    paginator = _ssm.get_paginator("describe_instance_information")
    for page in paginator.paginate(Filters=[{"Key": f"tag:{MANAGED_TAG}", "Values": ["true"]}]):
        for info in page["InstanceInformationList"]:
            iid = info["InstanceId"]
            tags = {t["Key"]: t["Value"] for t in
                    _ssm.list_tags_for_resource(ResourceType="ManagedInstance", ResourceId=iid)["TagList"]}
            store.put_device({
                "instance_id": iid,
                "computer_name": info.get("ComputerName") or info.get("Name") or iid,
                "platform": dispatcher.PLATFORMS.get(info.get("PlatformType"), "unknown"),
                "platform_name": info.get("PlatformName"),
                "platform_version": info.get("PlatformVersion"),
                "ip_address": info.get("IPAddress"),
                "ping_status": info.get("PingStatus"),
                "last_ping": info["LastPingDateTime"].isoformat() if info.get("LastPingDateTime") else None,
                "agent_version": info.get("AgentVersion"),
                "owner": (tags.get(OWNER_TAG) or "").lower() or None,
                "role": tags.get(ROLE_TAG),
            })
            count += 1
    log.info("Inventory sync: %d devices", count)
    return {"devices": count}
