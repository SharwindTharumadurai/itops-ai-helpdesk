"""Caller identity from the API Gateway JWT authorizer (Entra ID access token).

API Gateway has already verified signature, issuer, audience and expiry before the
Lambda runs; here we only read the claims.
"""
import os
import re

ADMIN_ROLE = os.environ.get("ADMIN_ROLE", "ITOps.Admin")


class Unauthorized(Exception):
    pass


class Caller:
    __slots__ = ("upn", "roles")

    def __init__(self, upn, roles):
        self.upn = upn
        self.roles = roles

    @property
    def is_admin(self):
        return ADMIN_ROLE in self.roles


def _parse_roles(raw):
    # HTTP API flattens array claims into a string like "[ITOps.Admin ITOps.Agent]".
    if isinstance(raw, list):
        return {str(r) for r in raw}
    return {r for r in re.split(r"[,\s]+", str(raw or "").strip().strip("[]")) if r}


def get_caller(event):
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims") or {}
    upn = claims.get("preferred_username") or claims.get("upn") or claims.get("unique_name") or claims.get("email")
    if not upn or "@" not in upn:
        raise Unauthorized("Token has no user principal name claim")
    return Caller(upn.strip().lower(), _parse_roles(claims.get("roles")))
