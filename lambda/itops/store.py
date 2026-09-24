"""DynamoDB single-table access.

Item layout (table: PK/SK, GSI1: GSI1PK/GSI1SK, GSI2: GSI2PK/GSI2SK)

  Ticket         PK=TICKET#<id>   SK=META                  GSI1=REQUESTER#<upn>/<created>  GSI2=STATUS#<status>/<created>
  Execution log  PK=TICKET#<id>   SK=EXEC#<cmd>#<instance> (TTL: expires_at)
  Command map    PK=CMD#<cmd>     SK=META                  (TTL: expires_at)
  Device         PK=DEVICE#<mi-id> SK=META                 GSI1=OWNER#<upn>/<name>         GSI2=DEVICE/<name>

Provisioned at 5 RCU/5 WCU for the table and each GSI = 15/15 total, inside the
25/25 always-free allowance. Execution logs expire via TTL to stay far below 25 GB.
"""
import datetime
import os
import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

_table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
EXEC_TTL_DAYS = int(os.environ.get("EXEC_TTL_DAYS", "180"))


class Conflict(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ttl(days):
    return int(time.time()) + days * 86400


def _clean(value):
    """DynamoDB rejects floats and prefers no None attributes."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def new_ticket_id():
    return f"TKT-{datetime.date.today():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


# ------------------------------------------------------------------------ tickets

def create_ticket(ticket):
    ts = now()
    status = ticket["status"]
    item = {
        **ticket,
        "PK": f"TICKET#{ticket['ticket_id']}", "SK": "META",
        "GSI1PK": f"REQUESTER#{ticket['requester']}", "GSI1SK": ts,
        "GSI2PK": f"STATUS#{status}", "GSI2SK": ts,
        "created_at": ts, "updated_at": ts,
        "history": [{"at": ts, "by": ticket.get("created_by", ticket["requester"]), "to": status,
                     "note": ticket.get("note")}],
    }
    _table.put_item(Item=_clean(item), ConditionExpression="attribute_not_exists(PK)")
    return _strip(item)


def get_ticket(ticket_id):
    item = _table.get_item(Key={"PK": f"TICKET#{ticket_id}", "SK": "META"}).get("Item")
    return _strip(item) if item else None


def transition(ticket_id, from_status, to_status, actor, note=None, **fields):
    """Atomically move a ticket between states. from_status may be a str or tuple.
    The condition makes double-approval / double-dispatch races impossible."""
    froms = (from_status,) if isinstance(from_status, str) else tuple(from_status)
    ts = now()
    names = {"#s": "status"}
    values = {
        ":to": to_status, ":ts": ts, ":g2": f"STATUS#{to_status}", ":empty": [],
        ":h": [{"at": ts, "by": actor, "from": "|".join(froms), "to": to_status, "note": note}],
    }
    sets = ["#s = :to", "updated_at = :ts", "GSI2PK = :g2",
            "history = list_append(if_not_exists(history, :empty), :h)"]
    for i, (k, v) in enumerate(f for f in fields.items() if f[1] is not None):
        names[f"#f{i}"] = k
        values[f":f{i}"] = v
        sets.append(f"#f{i} = :f{i}")
    for i, f in enumerate(froms):
        values[f":from{i}"] = f
    condition = "#s IN (" + ", ".join(f":from{i}" for i in range(len(froms))) + ")"
    try:
        resp = _table.update_item(
            Key={"PK": f"TICKET#{ticket_id}", "SK": "META"},
            UpdateExpression="SET " + ", ".join(sets),
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=_clean(values),
            ReturnValues="ALL_NEW",
        )
    except _table.meta.client.exceptions.ConditionalCheckFailedException as e:
        raise Conflict(f"Ticket {ticket_id} is not in state {'/'.join(froms)}") from e
    return _strip(resp["Attributes"])


def list_tickets_by_requester(upn, limit=25):
    resp = _table.query(IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq(f"REQUESTER#{upn}"),
                        ScanIndexForward=False, Limit=limit)
    return [_strip(i) for i in resp["Items"]]


def list_tickets_by_status(status, limit=50):
    resp = _table.query(IndexName="GSI2", KeyConditionExpression=Key("GSI2PK").eq(f"STATUS#{status}"),
                        ScanIndexForward=False, Limit=limit)
    return [_strip(i) for i in resp["Items"]]


# ------------------------------------------------------------- executions/commands

def put_command_map(command_id, ticket_id):
    _table.put_item(Item={"PK": f"CMD#{command_id}", "SK": "META", "ticket_id": ticket_id,
                          "expires_at": _ttl(30)})


def get_command_map(command_id):
    item = _table.get_item(Key={"PK": f"CMD#{command_id}", "SK": "META"}).get("Item")
    return item["ticket_id"] if item else None


def put_execution(ticket_id, command_id, instance_id, result):
    _table.put_item(Item=_clean({
        "PK": f"TICKET#{ticket_id}", "SK": f"EXEC#{command_id}#{instance_id}",
        "command_id": command_id, "instance_id": instance_id, "recorded_at": now(),
        "expires_at": _ttl(EXEC_TTL_DAYS), **result,
    }))


def list_executions(ticket_id):
    resp = _table.query(KeyConditionExpression=Key("PK").eq(f"TICKET#{ticket_id}") & Key("SK").begins_with("EXEC#"))
    return [_strip(i) for i in resp["Items"]]


# ------------------------------------------------------------------------ devices

def put_device(device):
    owner = (device.get("owner") or "unassigned").lower()
    name = device.get("computer_name") or device["instance_id"]
    _table.put_item(Item=_clean({
        **device,
        "PK": f"DEVICE#{device['instance_id']}", "SK": "META",
        "GSI1PK": f"OWNER#{owner}", "GSI1SK": name,
        "GSI2PK": "DEVICE", "GSI2SK": name,
        "synced_at": now(),
    }))


def get_device(instance_id):
    item = _table.get_item(Key={"PK": f"DEVICE#{instance_id}", "SK": "META"}).get("Item")
    return _strip(item) if item else None


def list_devices_by_owner(upn):
    resp = _table.query(IndexName="GSI1", KeyConditionExpression=Key("GSI1PK").eq(f"OWNER#{upn.lower()}"))
    return [_strip(i) for i in resp["Items"]]


def list_all_devices():
    items, kwargs = [], {"IndexName": "GSI2", "KeyConditionExpression": Key("GSI2PK").eq("DEVICE")}
    while True:
        resp = _table.query(**kwargs)
        items += [_strip(i) for i in resp["Items"]]
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _strip(item):
    return {k: v for k, v in item.items() if k not in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")}
