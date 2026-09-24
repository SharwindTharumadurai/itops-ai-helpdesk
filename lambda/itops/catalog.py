"""Action catalog: the ONLY set of things the AI is allowed to ask the system to do.

catalog.json is the single source of truth. The CDK stack reads the same file to
build the SSM Command documents, so the server-side validation here and the
allowedValues/allowedPattern enforced by SSM on the endpoint always agree.
"""
import json
import os
import re
from pathlib import Path

_DATA = json.loads((Path(__file__).parent / "catalog.json").read_text(encoding="utf-8"))
ACTIONS = _DATA["actions"]
CATEGORIES = _DATA["categories"]
MAX_PARAM_LEN = 256


class ValidationError(ValueError):
    pass


def get(action_id):
    action = ACTIONS.get(action_id) if isinstance(action_id, str) else None
    if not action:
        raise ValidationError(f"Unknown action '{action_id}'")
    return action


def graph_groups():
    """Display name -> Entra group object ID, from the GRAPH_GROUP_ALLOWLIST env var."""
    try:
        groups = json.loads(os.environ.get("GRAPH_GROUP_ALLOWLIST") or "{}")
    except json.JSONDecodeError:
        return {}
    return groups if isinstance(groups, dict) else {}


def allowed_values(spec):
    if spec.get("source") == "graph_groups":
        return sorted(graph_groups())
    return spec.get("allowedValues")


def validate_parameters(action_id, params, requester=None):
    """Return a clean {name: str} dict or raise ValidationError. Never trust LLM output."""
    specs = get(action_id).get("parameters", {})
    params = params or {}
    if not isinstance(params, dict):
        raise ValidationError("parameters must be an object")
    unknown = set(params) - set(specs)
    if unknown:
        raise ValidationError(f"Unexpected parameter(s): {', '.join(sorted(unknown))}")

    clean = {}
    for name, spec in specs.items():
        value = params.get(name)
        if value is None or value == "":
            if spec.get("default_from") == "requester" and requester:
                value = requester
            elif "default" in spec:
                value = spec["default"]
            else:
                raise ValidationError(f"Missing required parameter '{name}'")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValidationError(f"Parameter '{name}' must be a string")
        value = str(value).strip()
        if len(value) > MAX_PARAM_LEN:
            raise ValidationError(f"Parameter '{name}' is too long")

        allowed = allowed_values(spec)
        if allowed is not None:
            # Accept case differences from the LLM but always emit the canonical value.
            match = next((a for a in allowed if a.lower() == value.lower()), None)
            if match is None:
                raise ValidationError(f"'{value}' is not an approved value for '{name}'")
            value = match
        pattern = spec.get("pattern")
        if pattern and not re.fullmatch(pattern, value):
            raise ValidationError(f"Parameter '{name}' has an invalid format")
        clean[name] = value
    return clean


def prompt_catalog():
    """Compact catalog description embedded in the triage prompt."""
    lines = []
    for action_id, a in ACTIONS.items():
        params = []
        for name, spec in a.get("parameters", {}).items():
            allowed = allowed_values(spec)
            detail = f"one of {allowed}" if allowed else spec.get("description", "")
            if spec.get("default_from") == "requester":
                detail += " (omit to mean the requester)"
            elif "default" in spec:
                detail += f" (default {spec['default']})"
            params.append(f"{name}: {detail}")
        platforms = ",".join(a.get("platforms", [])) or "cloud"
        lines.append(
            f"- {action_id} [{a['category']}; {platforms}] {a['title']}. When: {a['hint']}"
            + (f" Params: {'; '.join(params)}" if params else "")
        )
    return "\n".join(lines)


def public_view():
    """Catalog as exposed to API clients (no internal document names)."""
    return [
        {
            "id": action_id,
            "title": a["title"],
            "category": a["category"],
            "approval": a["approval"],
            "platforms": a.get("platforms", []),
            "parameters": {n: {k: v for k, v in s.items() if k != "source"} | (
                {"allowedValues": allowed_values(s)} if s.get("source") else {})
                for n, s in a.get("parameters", {}).items()},
        }
        for action_id, a in ACTIONS.items()
    ]
