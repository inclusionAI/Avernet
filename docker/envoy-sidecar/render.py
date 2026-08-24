#!/usr/bin/env python3
"""
config-renderer: Render Envoy config from header-rules.yaml.

Reads header-rules.yaml (domain-matched set/remove rules) and produces
a complete envoy.yaml from the template, with VirtualHosts and MITM SNI
matching auto-generated from the rules.

Reference: ocb/dockers/poolab-sidecar/render.py (crypto logic removed)
"""

import argparse
import sys
from typing import Any

import yaml


# ---- Data structures ----

class SetRule:
    def __init__(self, header: str, value: str):
        self.header = header
        self.value = value


class OutboundRule:
    def __init__(self, name: str, domains: list[str], set_rules: list[SetRule], remove: list[str]):
        self.name = name
        self.domains = domains
        self.set_rules = set_rules
        self.remove = remove


class HeaderRulesConfig:
    def __init__(self, rules: list[OutboundRule] | None = None):
        self.rules = rules or []


# ---- Parse ----

def parse_config(data: dict[str, Any]) -> HeaderRulesConfig:
    """Parse header-rules.yaml into HeaderRulesConfig."""
    rules = []
    for r in data.get("rules", []):
        set_rules = [SetRule(s["header"], s["value"]) for s in r.get("set", [])]
        rules.append(OutboundRule(
            name=r.get("name", ""),
            domains=r.get("domains", []),
            set_rules=set_rules,
            remove=r.get("remove", []),
        ))
    return HeaderRulesConfig(rules=rules)


# ---- Render ----

def escape_yaml_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_headers_to_add(rules: list[SetRule], indent: str) -> str:
    """Render request_headers_to_add (set = OVERWRITE_IF_EXISTS_OR_ADD).

    indent is the list-item dash level:
      {indent}- header:
      {indent}    key: "..."
      {indent}    value: "..."
      {indent}  append_action: OVERWRITE_IF_EXISTS_OR_ADD
    """
    if not rules:
        return "[]"
    items = []
    for r in rules:
        items.append(
            f'{indent}- header:\n'
            f'{indent}    key: "{escape_yaml_string(r.header)}"\n'
            f'{indent}    value: "{escape_yaml_string(r.value)}"\n'
            f'{indent}  append_action: OVERWRITE_IF_EXISTS_OR_ADD'
        )
    return "\n" + "\n".join(items)


def render_headers_to_remove(rules: list[str], indent: str) -> str:
    """Render request_headers_to_remove."""
    if not rules:
        return "[]"
    items = [f'{indent}- "{escape_yaml_string(r)}"' for r in rules]
    return "\n" + "\n".join(items)


def render_virtual_host(rule: OutboundRule, idx: int) -> str:
    """Render a single VirtualHost."""
    item = "                    "   # 20 spaces: list-item dash indent
    prop = item + "  "             # 22 spaces: property indent
    child = prop + "  "            # 24 spaces: sub-list indent

    vh_name = rule.name or f"outbound_rule_{idx}"

    domains_yaml = "\n".join(f'{child}- "{d}"' for d in rule.domains)

    routes_yaml = (
        f'{child}- match:\n'
        f'{child}    prefix: "/"\n'
        f'{child}  route:\n'
        f'{child}    cluster: outbound_original_dst\n'
        f'{child}    timeout: 0s'
    )

    header_parts = []
    req_add = render_headers_to_add(rule.set_rules, child)
    req_remove = render_headers_to_remove(rule.remove, child)

    if req_add != "[]":
        header_parts.append(f"{prop}request_headers_to_add: {req_add}")
    if req_remove != "[]":
        header_parts.append(f"{prop}request_headers_to_remove: {req_remove}")

    vh = (
        f"{item}- name: {vh_name}\n"
        f"{prop}domains:\n"
        f"{domains_yaml}\n"
        f"{prop}routes:\n"
        f"{routes_yaml}"
    )

    if header_parts:
        vh += "\n" + "\n".join(header_parts)

    return vh


def render_virtual_hosts(rules: list[OutboundRule]) -> str:
    """Render all VirtualHosts."""
    if not rules:
        return "[]"
    return "\n" + "\n".join(render_virtual_host(r, i) for i, r in enumerate(rules))


def extract_sni_domains(rules: list[OutboundRule]) -> list[str]:
    """Extract MITM SNI domains from rules (exclude wildcard '*')."""
    domains = set()
    for rule in rules:
        for d in rule.domains:
            if d != "*":
                domains.add(d)
    return sorted(domains)


# ---- Main ----

def cmd_render(args):
    """Render Envoy config from header-rules.yaml."""
    # Read template
    try:
        with open(args.template, "r") as f:
            template = f.read()
    except FileNotFoundError:
        print(f"ERROR: failed to read template {args.template}", file=sys.stderr)
        sys.exit(1)

    # Read rules
    try:
        with open(args.rules, "r") as f:
            rules_data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"WARNING: failed to read rules {args.rules}, using defaults", file=sys.stderr)
        rules_data = {}
    except yaml.YAMLError as e:
        print(f"ERROR: failed to parse rules: {e}", file=sys.stderr)
        sys.exit(1)

    config = parse_config(rules_data)

    # Render VirtualHosts
    outbound_vhs = render_virtual_hosts(config.rules)

    # MITM SNI domains: auto-derived from rules (exclude catch-all '*')
    sni_domains = extract_sni_domains(config.rules)
    if sni_domains:
        mitm_sni_match = "[" + ", ".join(f'"{d}"' for d in sni_domains) + "]"
    else:
        mitm_sni_match = "[]"

    mitm_cert_dir = "/etc/sidecar/certs/mitm-ca"

    # Replace placeholders
    replacements = {
        "{{SIDECAR_ADMIN_PORT}}": str(args.admin_port),
        "{{SIDECAR_PROXY_PORT}}": str(args.proxy_port),
        "{{MITM_CERT_PATH}}": mitm_cert_dir,
        "{{MITM_SNI_MATCH}}": mitm_sni_match,
        "{{OUTBOUND_VIRTUAL_HOSTS}}": outbound_vhs,
        "{{OUTBOUND_HTTPS_VIRTUAL_HOSTS}}": outbound_vhs,
    }

    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)

    # Write output
    with open(args.output, "w") as f:
        f.write(result)

    # Stats
    total_set = sum(len(r.set_rules) for r in config.rules)
    total_rm = sum(len(r.remove) for r in config.rules)
    print(f"[config-renderer] Rendered Envoy config to {args.output}")
    print(f"[config-renderer]   {len(config.rules)} rules (set={total_set}, remove={total_rm})")
    print(f"[config-renderer]   MITM SNI domains: {sni_domains}")


def main():
    parser = argparse.ArgumentParser(description="Envoy config renderer")
    parser.add_argument("--template", default="/etc/envoy/envoy-template.yaml", help="Envoy config template path")
    parser.add_argument("--rules", default="/etc/sidecar/header-rules.yaml", help="Header rules YAML path")
    parser.add_argument("--output", default="/etc/envoy/envoy.yaml", help="Output config path")
    parser.add_argument("--proxy-port", type=int, default=38080, help="Outbound proxy port")
    parser.add_argument("--admin-port", type=int, default=38081, help="Envoy admin port")
    args = parser.parse_args()

    cmd_render(args)


if __name__ == "__main__":
    main()