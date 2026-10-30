"""Profile contract for the structured density dual-environment slice.

Two admitted leg shapes exist because the preprod wire topology split them
(live evidence 2026-10-31): the 15 structured Group/definition/run
obligations execute on the station-direct legacy face, while the two catalog
observations execute on the Gateway public front door with an explicit browser
origin.  Owned-local Singlebox keeps a single origin for both families, so the
templates leg defaults to the main profile there.
"""

from __future__ import annotations

import re
from typing import Any

from ..run_state import RunStateError

_ENV_REF = re.compile(r"[A-Z][A-Z0-9_]{2,100}\Z")

# The 14 reviewed structured-collaboration operations bound by the density
# main profile (Group create through delete, plain station-direct legacy face
# on preprod, public-envelope internal-v1 face on owned-local Singlebox).
DENSITY_STRUCTURED_OPERATIONS = frozenset({
    "structured.group.create",
    "structured.group.read",
    "structured.definition.read",
    "structured.definition.patch",
    "structured.definition.upgrade.missing",
    "structured.run.start",
    "structured.run.read",
    "structured.run.graph.read",
    "structured.run.node.read",
    "structured.run.pending.human.nodes",
    "structured.run.human.response.denied",
    "structured.run.cancel",
    "structured.run.rerun.aborted.denied",
    "structured.group.delete",
})

# The two reviewed catalog observations ride the Gateway public-v1 face on
# preprod (the station front door does not serve the catalog slice) and share
# the single Singlebox origin for owned-local runs.
DENSITY_TEMPLATES_OPERATIONS = frozenset({
    "structured_templates.list",
    "structured_templates.detail",
})


def _env_ref(value: Any, name: str) -> str:
    if not isinstance(value, str) or _ENV_REF.fullmatch(value) is None:
        raise RunStateError(f"{name} must be an environment variable reference")
    return value


def _exact_header_refs(profile: dict[str, Any], family: str) -> dict[str, str]:
    auth = profile.get("auth")
    if not isinstance(auth, dict) or not isinstance(auth.get("header_env"), dict):
        raise RunStateError(f"{family} profile must declare header_env references")
    headers = auth["header_env"]
    for key, value in headers.items():
        _env_ref(value, f"{family} auth.header_env.{key}")
    return headers


def _reject_secrets(profile: dict[str, Any], family: str) -> None:
    if profile.get("secrets") not in (None, {}):
        raise RunStateError(f"{family} profile must not carry secret values")


def validate_density_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Admit one structured-collaboration leg profile for the density journey."""
    if not isinstance(profile, dict):
        raise RunStateError("structured density profile must be an object")
    environment = profile.get("environment")
    entrypoint = profile.get("entrypoint")
    if not isinstance(environment, dict) or not isinstance(entrypoint, dict):
        raise RunStateError("structured density profile lacks environment or entrypoint")
    tier = environment.get("tier")
    kind = entrypoint.get("kind")
    if tier == "local":
        if (kind != "singlebox"
                or environment.get("lifecycle") != "local-provision"
                or environment.get("deployment") != "singlebox"
                or environment.get("provider") != "local"):
            raise RunStateError("local structured density leg must be owned-local singlebox")
    elif tier == "preprod":
        if (kind != "bcs-direct"
                or environment.get("lifecycle") != "remote-attach"
                or entrypoint.get("api_face") != "public-v1"
                or entrypoint.get("manifest_face") != "legacy"):
            raise RunStateError(
                "preprod structured density leg must be bcs-direct with the public-v1 "
                "entrypoint and legacy manifest face")
    else:
        raise RunStateError("structured density profile tier must be local or preprod")
    headers = _exact_header_refs(profile, "structured density")
    if set(headers) != {"Cookie", "Authorization"}:
        raise RunStateError(
            "structured density profile must bind the Human Cookie and the driver Bot Authorization")
    _reject_secrets(profile, "structured density")
    return profile


def validate_density_templates_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Admit the catalog leg profile for the density journey.

    Preprod catalog observations execute on the Gateway public front door with
    an explicit browser origin (the station front door returns 401 without the
    Gateway browser context); owned-local Singlebox keeps its single origin.
    The catalog slice is read-only and Human-Cookie authenticated; the driver
    Bot Authorization reference stays optional because no mutating catalog
    operation is admitted.  No accepted shape may carry inline secret values.
    """
    if not isinstance(profile, dict):
        raise RunStateError("structured density templates profile must be an object")
    environment = profile.get("environment")
    entrypoint = profile.get("entrypoint")
    if not isinstance(environment, dict) or not isinstance(entrypoint, dict):
        raise RunStateError("structured density templates profile lacks environment or entrypoint")
    tier = environment.get("tier")
    kind = entrypoint.get("kind")
    if tier == "local":
        if (kind != "singlebox"
                or environment.get("lifecycle") != "local-provision"
                or entrypoint.get("manifest_face") != "internal-v1"):
            raise RunStateError(
                "local structured density templates leg must be owned-local singlebox with the internal-v1 face")
    elif tier == "preprod":
        if (kind != "gateway"
                or environment.get("lifecycle") != "remote-attach"
                or entrypoint.get("api_face") != "public-v1"
                or entrypoint.get("manifest_face") != "internal-v1"):
            raise RunStateError(
                "preprod structured density templates leg must be gateway/public-v1/internal-v1")
        if not profile.get("browser_origin"):
            raise RunStateError(
                "preprod structured density templates leg requires the explicit Gateway browser origin")
    else:
        raise RunStateError("structured density templates profile tier must be local or preprod")
    headers = _exact_header_refs(profile, "structured density templates")
    if "Cookie" not in headers:
        raise RunStateError("structured density templates profile must bind the Human Cookie")
    if set(headers) - {"Cookie", "Authorization"}:
        raise RunStateError(
            "structured density templates profile may bind only Cookie and optional Authorization references")
    _reject_secrets(profile, "structured density templates")
    return profile
