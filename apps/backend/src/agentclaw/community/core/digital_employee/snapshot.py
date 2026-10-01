"""Stable approval fingerprint including package content hashes."""
import copy
import hashlib
import json
from typing import Any


def capability_digest(snapshot: dict[str, Any]) -> str:
    value = copy.deepcopy(snapshot)
    for skill in value.get("capabilities", {}).get("skills", []):
        # Signing a stable package again changes URL credentials, not its version.
        skill.pop("ossAddress", None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def runtime_capability_digest(snapshot: dict[str, Any]) -> str:
    """Compare deployed membership and bytes, independent of display metadata."""
    caps = snapshot["capabilities"]
    value = {
        "skills": sorted((item["skillId"], item["name"], snapshot["package_hashes"][item["skillId"]]) for item in caps["skills"]),
        "mcps": sorted((item["mcpServerCode"], item["identityMode"]) for item in caps["mcps"]),
        "clis": sorted((item["cliCode"], item["identityMode"]) for item in caps["clis"]),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
