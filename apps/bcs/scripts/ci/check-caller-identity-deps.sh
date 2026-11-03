#!/usr/bin/env bash
# Caller identity plugins must not reintroduce private implementations or Service API coupling.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
cargo metadata --format-version 1 --no-deps --offline | python3 -c '
import json, sys
packages = {p["name"]: p for p in json.load(sys.stdin)["packages"]}
def deps(name):
    return {d["name"] for d in packages[name]["dependencies"] if d["kind"] is None}
assert "bcs-caller-identity-agentpass" not in packages, "private adapter in public workspace"
assert deps("bcs-caller-identity-api") <= {"bcs-domain", "bcs-config-api", "async-trait", "thiserror", "inventory"}
assert "bcs-caller-identity-api" not in deps("bcs-service-api"), "Service API must not depend on caller Plugin API"
assert "bcs-service-api" not in deps("bcs-caller-identity-jwt"), "JWT must implement Plugin API directly"
assert "bcs-caller-identity-api" in deps("bcs-caller-identity-jwt")
assert "bcs-caller-identity-jwt" not in deps("bcs-message-flow"), "core must not select JWT"
print("PASS: caller identity plugin dependency boundaries")
'
