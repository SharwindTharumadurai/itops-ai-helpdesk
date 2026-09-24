"""Microsoft Graph (Entra ID / M365) account automation via app-only client credentials.

Required Entra app registration (see docs/ENTRA_SETUP.md):
  Application permissions: User.ReadWrite.All, GroupMember.ReadWrite.All
  Directory role on the service principal: User Administrator (needed for app-only
  password resets; Entra itself then blocks resets of higher-privileged admins).
"""
import json
import os
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3

import catalog

TENANT = os.environ.get("GRAPH_TENANT_ID", "")
CLIENT_ID = os.environ.get("GRAPH_CLIENT_ID", "")
SECRET_PARAM = os.environ.get("GRAPH_SECRET_PARAM", "/itops/graph/client-secret")
ALLOWED_DOMAINS = {d.strip().lower() for d in os.environ.get("ALLOWED_UPN_DOMAINS", "").split(",") if d.strip()}
PROTECTED_UPNS = {u.strip().lower() for u in os.environ.get("PROTECTED_UPNS", "").split(",") if u.strip()}
GRAPH = "https://graph.microsoft.com/v1.0"

_ssm = boto3.client("ssm")
_cache = {"secret": None, "token": None, "exp": 0}


class GraphError(Exception):
    pass


def configured():
    return bool(TENANT and CLIENT_ID)


def _token():
    if _cache["token"] and _cache["exp"] > time.time() + 60:
        return _cache["token"]
    if _cache["secret"] is None:
        _cache["secret"] = _ssm.get_parameter(Name=SECRET_PARAM, WithDecryption=True)["Parameter"]["Value"]
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": _cache["secret"],
        "scope": "https://graph.microsoft.com/.default",
    }).encode()
    url = f"https://login.microsoftonline.com/{urllib.parse.quote(TENANT)}/oauth2/v2.0/token"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=form, method="POST"), timeout=15) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        # Forget the cached secret so a corrected value in Parameter Store is picked up on the
        # next call instead of when this Lambda container happens to be recycled.
        _cache["secret"] = None
        try:
            err = json.loads(e.read())
            # First line of error_description carries the AADSTS code, e.g.
            # "AADSTS7000215: Invalid client secret provided." It never contains the secret.
            reason = (err.get("error_description") or err.get("error") or "").splitlines()[0][:200]
        except (ValueError, IndexError):
            reason = "no detail"
        raise GraphError(f"Token request failed ({e.code}): {reason}") from e
    _cache["token"], _cache["exp"] = body["access_token"], time.time() + int(body.get("expires_in", 3599))
    return _cache["token"]


def _call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(GRAPH + path, data=data, method=method, headers={
        "authorization": f"Bearer {_token()}", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read()).get("error", {})
            msg = f"{err.get('code')}: {err.get('message')}"
        except ValueError:
            msg = "no detail"
        raise GraphError(f"Graph {method} {path.split('?')[0]} -> {e.code} {msg}") from e


def _user_path(upn):
    return "/users/" + urllib.parse.quote(upn, safe="@.")


def _check_upn(upn):
    upn = upn.lower()
    if ALLOWED_DOMAINS and upn.split("@")[-1] not in ALLOWED_DOMAINS:
        raise GraphError(f"Domain of {upn} is not in ALLOWED_UPN_DOMAINS")
    if upn in PROTECTED_UPNS:
        raise GraphError(f"{upn} is a protected account and cannot be changed by automation")
    return upn


def temporary_password(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%*-_+?"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(not c.isalnum() for c in pw)):
            return pw


# ------------------------------------------------------------------------ actions

def reset_password(p):
    upn = _check_upn(p["UserPrincipalName"])
    pw = temporary_password()
    _call("PATCH", _user_path(upn), {"passwordProfile": {"password": pw, "forceChangePasswordNextSignIn": True}})
    # Kill existing refresh tokens so a possible attacker's session dies with the old password.
    _call("POST", _user_path(upn) + "/revokeSignInSessions")
    return {"message": f"Password reset for {upn}; change required at next sign-in.", "temporary_password": pw}


def revoke_sessions(p):
    upn = _check_upn(p["UserPrincipalName"])
    _call("POST", _user_path(upn) + "/revokeSignInSessions")
    return {"message": f"All refresh tokens revoked for {upn}. Apps will require sign-in again within ~1 hour."}


def add_group_member(p):
    upn = _check_upn(p["UserPrincipalName"])
    group_id = catalog.graph_groups().get(p["Group"])
    if not group_id:
        raise GraphError(f"Group '{p['Group']}' is not in the allowlist")
    group = _call("GET", f"/groups/{urllib.parse.quote(group_id)}?$select=id,displayName,isAssignableToRole")
    if group.get("isAssignableToRole"):
        raise GraphError("Refusing to modify a role-assignable (privileged) group")
    user = _call("GET", _user_path(upn) + "?$select=id")
    try:
        _call("POST", f"/groups/{group_id}/members/$ref",
              {"@odata.id": f"{GRAPH}/directoryObjects/{user['id']}"})
    except GraphError as e:
        if "already exist" not in str(e):
            raise
        return {"message": f"{upn} was already a member of {group['displayName']}."}
    return {"message": f"Added {upn} to {group['displayName']}."}


def create_user(p):
    upn = _check_upn(p["UserPrincipalName"])
    pw = temporary_password()
    nickname = "".join(c for c in upn.split("@")[0] if c.isalnum() or c in "._-")[:64]
    user = _call("POST", "/users", {
        "accountEnabled": True,
        "displayName": p["DisplayName"],
        "mailNickname": nickname,
        "userPrincipalName": upn,
        "passwordProfile": {"password": pw, "forceChangePasswordNextSignIn": True},
    })
    return {"message": f"Created {upn} (object id {user['id']}). Assign licences separately.",
            "temporary_password": pw}


_ACTIONS = {
    "entra_reset_password": reset_password,
    "entra_revoke_sessions": revoke_sessions,
    "entra_add_group_member": add_group_member,
    "entra_create_user": create_user,
}


def run(action_id, params):
    if not configured():
        raise GraphError("Microsoft Graph is not configured (GRAPH_TENANT_ID / GRAPH_CLIENT_ID)")
    return _ACTIONS[action_id](params)
