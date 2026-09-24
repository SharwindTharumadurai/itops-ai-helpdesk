#!/usr/bin/env python3
"""Minimal ITOps API client for testing (stdlib only).

Token: uses the Azure CLI (`az login` first). The API app registration must list the
Azure CLI (04b07795-8ddb-461a-bbee-02f9e1bf7b46) as an authorized client application.

  set ITOPS_API=https://xxxx.execute-api.<region>.amazonaws.com
  set ITOPS_APP_ID=api://<api-app-client-id>
  python itops_cli.py chat "My laptop can't open any websites"
  python itops_cli.py tickets [--status PENDING_APPROVAL]
  python itops_cli.py ticket TKT-...
  python itops_cli.py approve TKT-... [--sha256 <hash>]
  python itops_cli.py reject TKT-... --reason "not needed"
  python itops_cli.py devices
  python itops_cli.py generate --device mi-... --goal "Repair the Windows Search index"
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


def token():
    az = shutil.which("az") or shutil.which("az.cmd")
    if not az:
        sys.exit("Azure CLI not found; install it or set ITOPS_TOKEN")
    return subprocess.check_output(
        [az, "account", "get-access-token", "--resource", os.environ["ITOPS_APP_ID"],
         "--query", "accessToken", "-o", "tsv"], text=True).strip()


def call(method, path, body=None):
    base = os.environ["ITOPS_API"].rstrip("/")
    bearer = os.environ.get("ITOPS_TOKEN") or token()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={
        "authorization": f"Bearer {bearer}", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"http_status": e.code, **json.loads(e.read() or b"{}")}


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chat"); c.add_argument("message"); c.add_argument("--device")
    t = sub.add_parser("tickets"); t.add_argument("--status")
    sub.add_parser("ticket").add_argument("ticket_id")
    a = sub.add_parser("approve"); a.add_argument("ticket_id"); a.add_argument("--sha256"); a.add_argument("--comment")
    r = sub.add_parser("reject"); r.add_argument("ticket_id"); r.add_argument("--reason", default="")
    sub.add_parser("devices")
    sub.add_parser("catalog")
    g = sub.add_parser("generate"); g.add_argument("--device", required=True); g.add_argument("--goal", required=True)
    args = p.parse_args()

    if args.cmd == "chat":
        out = call("POST", "/chat", {"message": args.message, "device_id": args.device})
    elif args.cmd == "tickets":
        out = call("GET", "/tickets" + (f"?status={args.status}" if args.status else ""))
    elif args.cmd == "ticket":
        out = call("GET", f"/tickets/{args.ticket_id}")
    elif args.cmd == "approve":
        out = call("POST", f"/tickets/{args.ticket_id}/approve", {"sha256": args.sha256, "comment": args.comment})
    elif args.cmd == "reject":
        out = call("POST", f"/tickets/{args.ticket_id}/reject", {"reason": args.reason})
    elif args.cmd == "devices":
        out = call("GET", "/devices")
    elif args.cmd == "catalog":
        out = call("GET", "/catalog")
    else:
        out = call("POST", "/scripts/generate", {"device_id": args.device, "goal": args.goal})
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
