"""HTTP API entry point (API Gateway HTTP API, payload format 2.0).

Request lifecycle for POST /chat:

  user text --> LLM triage (untrusted) --> catalog validation --> target resolution
      --> policy decision:  auto  -> dispatch now (SSM / Graph)
                            admin -> PENDING_APPROVAL (admin calls /approve)
                            none  -> NEEDS_HUMAN / NEEDS_INFO

Ticket states: TRIAGED* -> NEEDS_INFO | NEEDS_HUMAN | PENDING_APPROVAL | APPROVED
               APPROVED -> DISPATCHED -> SUCCESS | FAILED     (SSM, updated by ssm_events)
               APPROVED -> SUCCESS | FAILED                   (Graph, synchronous)
               PENDING_APPROVAL -> REJECTED;  generated scripts may start as BLOCKED
"""
import base64
import hashlib
import json
import logging
import os
import time
from decimal import Decimal

import ai
import auth
import catalog
import dispatcher
import graph
import guardrails
import store
from catalog import ValidationError

log = logging.getLogger()
log.setLevel(logging.INFO)

# Leave this much of the Lambda's 29 s for DynamoDB writes and the SSM dispatch after the LLM call.
POST_LLM_RESERVE_SECONDS = 5
_request = {"deadline": None}
REQUIRE_TWO_PERSON = os.environ.get("REQUIRE_TWO_PERSON", "true").lower() == "true"
ALLOW_GENERATED_SCRIPTS = os.environ.get("ALLOW_GENERATED_SCRIPTS", "true").lower() == "true"
MAX_TEXT = 2000
ROUTES = {}


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def route(key):
    def deco(fn):
        ROUTES[key] = fn
        return fn
    return deco


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o).__name__)


def respond(status, body):
    return {"statusCode": status,
            "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": json.dumps(body, default=_json_default)}


def _body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    if len(raw) > 16384:
        raise HttpError(413, "Request body too large")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HttpError(400, "Body must be valid JSON")
    if not isinstance(data, dict):
        raise HttpError(400, "Body must be a JSON object")
    return data


def _require_admin(caller):
    if not caller.is_admin:
        raise HttpError(403, f"Requires the {auth.ADMIN_ROLE} app role")


def _audit(event_name, caller, **kw):
    # Structured audit line -> CloudWatch Logs (retention set in CDK).
    log.info(json.dumps({"audit": event_name, "actor": caller if isinstance(caller, str) else caller.upn, **kw},
                        default=str))


def handler(event, context):
    fn = ROUTES.get(event.get("routeKey", ""))
    if not fn:
        return respond(404, {"error": "Not found"})
    _request["deadline"] = (time.time() + context.get_remaining_time_in_millis() / 1000 - POST_LLM_RESERVE_SECONDS
                            if context else None)
    try:
        caller = auth.get_caller(event)
        return respond(200, fn(event, caller))
    except auth.Unauthorized as e:
        return respond(401, {"error": str(e)})
    except HttpError as e:
        return respond(e.status, {"error": str(e)})
    except ValidationError as e:
        return respond(400, {"error": str(e)})
    except store.Conflict as e:
        return respond(409, {"error": str(e)})
    except ai.LLMError as e:
        log.warning("LLM unavailable: %s", e)
        return respond(503, {"error": "The AI service is busy or unavailable right now. Please try again in a minute."})
    except Exception:
        log.exception("Unhandled error")
        return respond(500, {"error": "Internal error"})


# ------------------------------------------------------------------ planning logic

def _resolve_device(caller, device_id, hint, action):
    """Pick the one device this action should run on. Non-admins may only target
    devices whose ITOps:Owner tag matches their UPN."""
    platforms = set(action.get("platforms", []))
    if device_id:
        device = store.get_device(str(device_id))
        if not device:
            return None, f"Device {device_id} is not registered."
        if not caller.is_admin and device.get("owner") != caller.upn:
            raise HttpError(403, "You can only run fixes on devices assigned to you.")
        candidates = [device]
    else:
        candidates = store.list_devices_by_owner(caller.upn)
        if hint and len(candidates) > 1:
            named = [d for d in candidates if hint.lower() in (d.get("computer_name") or "").lower()]
            candidates = named or candidates
    supported = [d for d in candidates if d.get("platform") in platforms]
    if len(supported) == 1:
        return supported[0], None
    if not supported:
        return None, (f"No registered device of yours supports this fix (needs {', '.join(sorted(platforms))}). "
                      "IT will follow up.")
    names = ", ".join(f"{d['computer_name']} ({d['instance_id']})" for d in supported)
    return None, f"You have several devices - resend with device_id set to one of: {names}"


def _plan(triage, caller, device_id):
    action_id = triage.get("action_id")
    if not action_id:
        return {"status": "NEEDS_HUMAN", "note": "No automated fix applies; queued for IT staff."}
    try:
        action = catalog.get(action_id)
        params = catalog.validate_parameters(action_id, triage.get("parameters"), requester=caller.upn)
    except ValidationError as e:
        return {"status": "NEEDS_HUMAN", "note": f"AI proposal rejected by validator: {e}"}

    plan = {"action_id": action_id, "parameters": params, "executor": action["executor"]}
    if action["executor"] == "ssm":
        if action.get("target_role"):
            plan["target_role"] = action["target_role"]
            needs_approval = True
        else:
            device, note = _resolve_device(caller, device_id, triage.get("device_hint"), action)
            if not device:
                return {**plan, "status": "NEEDS_INFO", "note": note}
            plan.update(target_instance=device["instance_id"], platform=device["platform"])
            if device.get("ping_status") != "Online":
                plan["note"] = "Device is currently offline; the fix will run when it reconnects (within 1 hour)."
            needs_approval = action["approval"] != "auto"
    else:
        plan["target_user"] = params.get("UserPrincipalName", "").lower() or None
        needs_approval = not (action["approval"] == "auto_self" and plan["target_user"] == caller.upn)
    plan["status"] = "PENDING_APPROVAL" if needs_approval else "APPROVED"
    return plan


def _role_instance(role):
    online = [d for d in store.list_all_devices() if d.get("role") == role and d.get("ping_status") == "Online"]
    if not online:
        raise HttpError(503, f"No online managed server with ITOps:Role={role}")
    return online[0]["instance_id"]


def _execute(ticket, actor):
    """Run an APPROVED ticket. Returns extra response fields."""
    tid = ticket["ticket_id"]
    try:
        if ticket.get("kind") == "generated_script":
            cmd = dispatcher.send_script(ticket["platform"], ticket["script"], [ticket["target_instance"]], tid)
            store.put_command_map(cmd, tid)
            store.transition(tid, "APPROVED", "DISPATCHED", actor, command_id=cmd)
            _audit("dispatch_generated_script", actor, ticket=tid, command_id=cmd, sha256=ticket["script_sha256"])
            return {"command_id": cmd, "status": "DISPATCHED"}

        action = catalog.get(ticket["action_id"])
        if action["executor"] == "ssm":
            instance = ticket.get("target_instance") or _role_instance(action["target_role"])
            cmd = dispatcher.send_action(action, [instance], ticket.get("parameters", {}), tid)
            store.put_command_map(cmd, tid)
            store.transition(tid, "APPROVED", "DISPATCHED", actor, command_id=cmd, target_instance=instance)
            _audit("dispatch_action", actor, ticket=tid, action=ticket["action_id"], instance=instance, command_id=cmd)
            return {"command_id": cmd, "status": "DISPATCHED"}

        result = graph.run(ticket["action_id"], ticket.get("parameters", {}))
        secret = result.pop("temporary_password", None)
        store.transition(tid, "APPROVED", "SUCCESS", actor, note=result["message"], result=result)
        _audit("graph_action", actor, ticket=tid, action=ticket["action_id"], target=ticket.get("target_user"))
        out = {"status": "SUCCESS", "result": result}
        if secret:
            # Returned exactly once to the approving admin over TLS; never stored or logged.
            out["temporary_password"] = secret
            out["notice"] = "Shown once and not stored. Give it to the user out-of-band (phone / in person)."
        return out
    except (graph.GraphError, HttpError) as e:
        store.transition(tid, "APPROVED", "FAILED", actor, note=str(e)[:500])
        return {"status": "FAILED", "error": str(e)}
    except Exception as e:
        log.exception("Dispatch failed for %s", tid)
        store.transition(tid, "APPROVED", "FAILED", actor, note=f"Dispatch error: {type(e).__name__}")
        return {"status": "FAILED", "error": "Dispatch failed; see logs."}


_STATUS_MESSAGES = {
    "DISPATCHED": "An automated fix is running on your device now.",
    "SUCCESS": "Done - the automated action completed.",
    "PENDING_APPROVAL": "This needs approval from IT before it runs. You'll see the status update here.",
    "NEEDS_HUMAN": "An IT technician will pick this up.",
    "NEEDS_INFO": "We need a bit more information (see note).",
    "FAILED": "The automated action failed; an IT technician will follow up.",
}


# ------------------------------------------------------------------------ routes

@route("POST /chat")
def chat(event, caller):
    body = _body(event)
    text = str(body.get("message", "")).strip()[:MAX_TEXT]
    if not text:
        raise HttpError(400, "'message' is required")

    devices = store.list_devices_by_owner(caller.upn)
    try:
        triage = ai.triage(text, caller.upn, devices, deadline=_request["deadline"])
    except ai.LLMError as e:
        log.warning("Triage failed: %s", e)
        triage = {**ai.normalise_triage({"summary": text[:120], "reply": "Your request has been logged for IT staff."}),
                  "ai_provider": None}
        # Say plainly that no AI judged this ticket, so a technician doesn't read the
        # empty action as "the AI decided nothing applies".
        plan = {"status": "NEEDS_HUMAN", "note": "AI triage unavailable - please review this ticket manually."}
    else:
        plan = _plan(triage, caller, body.get("device_id"))
    ticket = store.create_ticket({
        "ticket_id": store.new_ticket_id(),
        "kind": "chat",
        "requester": caller.upn,
        "request_text": text,
        "category": triage["category"],
        "priority": triage["priority"],
        "summary": triage["summary"],
        "ai_confidence": triage["confidence"],
        "ai_provider": triage["ai_provider"],
        **{k: v for k, v in plan.items() if k != "executor"},
    })
    _audit("ticket_created", caller, ticket=ticket["ticket_id"], action=plan.get("action_id"), status=plan["status"])

    result = {}
    if plan["status"] == "APPROVED":
        result = _execute(ticket, caller.upn)
    status = result.get("status", plan["status"])
    return {
        "ticket_id": ticket["ticket_id"],
        "status": status,
        "status_message": _STATUS_MESSAGES.get(status, ""),
        "category": triage["category"],
        "priority": triage["priority"],
        "proposed_action": plan.get("action_id"),
        "ai_provider": triage["ai_provider"],
        "note": plan.get("note"),
        "reply": triage["reply"],
        **{k: v for k, v in result.items() if k not in ("status", "temporary_password", "notice")},
    }


@route("POST /scripts/generate")
def generate(event, caller):
    """Admin-only: ask the LLM to draft a script for a problem the catalog doesn't cover.
    The draft is NEVER executed here - it waits for hash-pinned (two-person) approval."""
    _require_admin(caller)
    if not ALLOW_GENERATED_SCRIPTS:
        raise HttpError(403, "Generated scripts are disabled (allowGeneratedScripts=false)")
    body = _body(event)
    goal = str(body.get("goal", "")).strip()[:MAX_TEXT]
    device = store.get_device(str(body.get("device_id", "")))
    if not goal or not device:
        raise HttpError(400, "'goal' and a registered 'device_id' are required")

    draft = ai.generate_script(device["platform"], goal, deadline=_request["deadline"])
    violations = guardrails.check_script(device["platform"], draft["script"])
    digest = hashlib.sha256(draft["script"].encode("utf-8")).hexdigest()
    ticket = store.create_ticket({
        "ticket_id": store.new_ticket_id(),
        "kind": "generated_script",
        "requester": caller.upn,
        "created_by": caller.upn,
        "request_text": goal,
        "category": "other",
        "priority": "P3",
        "summary": f"Generated {device['platform']} script: {goal[:80]}",
        "status": "BLOCKED" if violations else "PENDING_APPROVAL",
        "platform": device["platform"],
        "target_instance": device["instance_id"],
        "script": draft["script"],
        "script_sha256": digest,
        "explanation": draft["explanation"],
        "ai_risk": draft["risk"],
        "ai_provider": draft["ai_provider"],
        "violations": violations or None,
        "note": "Blocked by static guardrails" if violations else "Awaiting review",
    })
    _audit("script_generated", caller, ticket=ticket["ticket_id"], sha256=digest, violations=len(violations))
    return {k: ticket.get(k) for k in ("ticket_id", "status", "script", "script_sha256", "explanation",
                                        "ai_risk", "violations", "target_instance", "platform")}


@route("POST /tickets/{ticket_id}/approve")
def approve(event, caller):
    _require_admin(caller)
    tid = event["pathParameters"]["ticket_id"]
    body = _body(event)
    ticket = store.get_ticket(tid)
    if not ticket:
        raise HttpError(404, "Ticket not found")
    if ticket["status"] != "PENDING_APPROVAL":
        raise HttpError(409, f"Ticket is {ticket['status']}, not PENDING_APPROVAL")
    if ticket.get("kind") == "generated_script":
        if body.get("sha256") != ticket["script_sha256"]:
            raise HttpError(409, "sha256 does not match the stored script. Review the exact script and send its sha256.")
        if REQUIRE_TWO_PERSON and caller.upn == ticket.get("created_by"):
            raise HttpError(403, "Two-person rule: a different admin must approve a script you generated.")
    ticket = store.transition(tid, "PENDING_APPROVAL", "APPROVED", caller.upn,
                              note=str(body.get("comment", ""))[:300] or None, approved_by=caller.upn)
    _audit("ticket_approved", caller, ticket=tid)
    return {"ticket_id": tid, **_execute(ticket, caller.upn)}


@route("POST /tickets/{ticket_id}/reject")
def reject(event, caller):
    _require_admin(caller)
    tid = event["pathParameters"]["ticket_id"]
    reason = str(_body(event).get("reason", "")).strip()[:300] or "Rejected by IT"
    store.transition(tid, ("PENDING_APPROVAL", "BLOCKED", "NEEDS_HUMAN", "NEEDS_INFO"), "REJECTED", caller.upn, note=reason)
    _audit("ticket_rejected", caller, ticket=tid)
    return {"ticket_id": tid, "status": "REJECTED"}


@route("GET /tickets/{ticket_id}")
def get_ticket(event, caller):
    tid = event["pathParameters"]["ticket_id"]
    ticket = store.get_ticket(tid)
    if not ticket or (not caller.is_admin and ticket["requester"] != caller.upn):
        raise HttpError(404, "Ticket not found")
    # Fallback refresh in case the EventBridge status event was missed.
    if ticket["status"] == "DISPATCHED" and ticket.get("command_id") and ticket.get("target_instance"):
        inv = dispatcher.get_invocation(ticket["command_id"], ticket["target_instance"])
        if inv and inv["status"] in dispatcher.TERMINAL:
            store.put_execution(tid, ticket["command_id"], ticket["target_instance"], inv)
            try:
                ticket = store.transition(tid, "DISPATCHED", "SUCCESS" if inv["status"] == "Success" else "FAILED",
                                          "system", note=inv.get("status_details"))
            except store.Conflict:
                ticket = store.get_ticket(tid)
    ticket["executions"] = store.list_executions(tid)
    if not caller.is_admin:
        ticket.pop("script", None)
    return ticket


@route("GET /tickets")
def list_tickets(event, caller):
    qs = event.get("queryStringParameters") or {}
    if qs.get("status"):
        _require_admin(caller)
        return {"tickets": store.list_tickets_by_status(qs["status"].upper())}
    return {"tickets": store.list_tickets_by_requester(caller.upn)}


@route("GET /devices")
def list_devices(event, caller):
    return {"devices": store.list_all_devices() if caller.is_admin else store.list_devices_by_owner(caller.upn)}


@route("GET /catalog")
def get_catalog(event, caller):
    return {"actions": catalog.public_view()}
