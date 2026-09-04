#!/usr/bin/env python3
"""
config-renderer: Render Envoy config from header-rules.yaml.

Reads header-rules.yaml (domain-matched set/remove rules) and produces
a complete envoy.yaml from the template, with VirtualHosts, MITM SNI
matching, and Lua-based placeholder substitution auto-generated.

Rules with a "placeholder" field use a Lua filter for substring
replacement within the existing header value (e.g. replace only
${API-KEY} inside "Bearer ${API-KEY}"). Rules without placeholder
use request_headers_to_add for whole-value overwrite.
"""

import argparse
import json
import re
import sys
from typing import Any

import yaml


# ---- Data structures ----

class SetRule:
    def __init__(self, header: str, value: str, placeholder: str | None = None):
        self.header = header
        self.value = value
        self.placeholder = placeholder


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
        set_rules = [
            SetRule(
                s["header"],
                s["value"],
                s.get("placeholder"),
            )
            for s in r.get("set", [])
        ]
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
    """Render request_headers_to_add for non-placeholder rules only."""
    non_ph = [r for r in rules if not r.placeholder]
    if not non_ph:
        return "[]"
    items = []
    for r in non_ph:
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


# ---- Lua code generation for placeholder rules ----

def escape_lua_string(s: str) -> str:
    """Escape a string for safe embedding in Lua double-quoted strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def escape_lua_pattern(s: str) -> str:
    """Escape Lua pattern special chars (^$()%.[]*+-?) with % prefix."""
    special = set('^$().%[]*+-?')
    result = []
    for c in s:
        if c in special:
            result.append('%')
        result.append(c)
    return ''.join(result)


def render_lua_code(rules: list[OutboundRule]) -> str:
    """Generate Lua inline code for placeholder-based header replacement.

    Only rules with set_rules that have a placeholder field are handled.
    The Lua filter does string.gsub on the existing header value to
    replace only the placeholder substring with the configured value.
    """
    ph_entries = []  # (header, placeholder_pattern, value)
    for rule in rules:
        for s in rule.set_rules:
            if s.placeholder:
                ph_entries.append((
                    s.header,
                    escape_lua_pattern(s.placeholder),
                    escape_lua_string(s.value),
                ))

    if not ph_entries:
        return ""

    lines = [
        "function envoy_on_request(handle)",
    ]
    for header, ph_pat, val in ph_entries:
        escaped_header = escape_lua_string(header)
        lines.extend([
            f'  local h = handle:headers():get("{escaped_header}")',
            f'  if h then',
            f'    h = string.gsub(h, "{ph_pat}", "{val}")',
            f'    handle:headers():replace("{escaped_header}", h)',
            f'  end',
        ])
    lines.append("end")
    return "\n".join(lines)


def has_placeholder_rules(rules: list[OutboundRule]) -> bool:
    """Check if any rule has placeholder-based set rules."""
    return any(
        s.placeholder
        for r in rules
        for s in r.set_rules
    )


def render_lua_filter_config(lua_code: str) -> str:
    """Render the Lua filter YAML block, or empty string if no code.

    In the template, {{LUA_FILTER}} is placed before the router filter
    at 18-space indent. When non-empty, we output the Lua filter entry
    followed by a newline + 18 spaces so the router filter line stays
    aligned. When empty, the template's trailing spaces produce the
    router line correctly.
    """
    if not lua_code:
        return ""
    code_json = json.dumps(lua_code)
    # 18 spaces = indent of http_filters items in both MITM and HTTP chains
    indent = "                  "
    return (
        f'{indent}- name: envoy.filters.http.lua\n'
        f'{indent}  typed_config:\n'
        f'{indent}    "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua\n'
        f'{indent}    inline_code: {code_json}\n'
    )


# ---- VirtualHost rendering ----

def render_virtual_host(rule: OutboundRule, idx: int, cluster: str = "outbound_original_dst") -> str:
    """Render a single VirtualHost."""
    item = "                    "   # 20 spaces: list-item dash indent
    prop = item + "  "             # 22 spaces: property indent
    child = prop + "  "            # 24 spaces: sub-list indent

    vh_name = rule.name or f"outbound_rule_{idx}"

    domains_yaml = "\n".join(f'{child}- "{d}"' for d in rule.domains)

    # upgrade_configs is required for WebSocket: without it Envoy treats the
    # GET as a plain HTTP request, strips the hop-by-hop Upgrade headers, and
    # the upstream 403/400s the non-websocket handshake.
    routes_yaml = (
        f'{child}- match:\n'
        f'{child}    prefix: "/"\n'
        f'{child}  route:\n'
        f'{child}    cluster: {cluster}\n'
        f'{child}    timeout: 0s\n'
        f'{child}    upgrade_configs:\n'
        f'{child}      - upgrade_type: websocket'
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


def render_virtual_hosts(rules: list[OutboundRule], cluster: str = "outbound_original_dst") -> str:
    """Render all VirtualHosts, merging rules with identical domain sets.

    When no rules exist, emit a default catch-all VirtualHost (domain '*')
    that routes all traffic to the original destination without modifying
    headers. This ensures Envoy passes traffic through instead of 404.
    """
    if not rules:
        # Default catch-all: no header ops, just pass-through
        return "\n" + render_default_virtual_host(cluster)

    groups: dict[tuple[str, ...], OutboundRule] = {}
    order: list[tuple[str, ...]] = []
    for r in rules:
        key = tuple(sorted(r.domains))
        if key not in groups:
            groups[key] = OutboundRule(
                name=r.name, domains=r.domains, set_rules=[], remove=[]
            )
            order.append(key)
        g = groups[key]
        g.set_rules.extend(r.set_rules)
        g.remove.extend(r.remove)

    merged = [groups[k] for k in order]
    return "\n" + "\n".join(render_virtual_host(r, i, cluster) for i, r in enumerate(merged))


def render_default_virtual_host(cluster: str = "outbound_original_dst_http") -> str:
    """Render a default catch-all VirtualHost with no header operations.

    Used when no rules exist — allows Envoy to pass traffic through
    to the original destination instead of returning 404.
    """
    item = "                    "   # 20 spaces
    prop = item + "  "             # 22 spaces
    child = prop + "  "            # 24 spaces

    return (
        f"{item}- name: default_pass_through\n"
        f"{prop}domains:\n"
        f'{child}- "*"\n'
        f"{prop}routes:\n"
        f'{child}- match:\n'
        f'{child}    prefix: "/"\n'
        f'{child}  route:\n'
        f'{child}    cluster: {cluster}\n'
        f'{child}    timeout: 0s\n'
        f'{child}    upgrade_configs:\n'
        f'{child}      - upgrade_type: websocket'
    )


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

    # Split rules by domain type
    mitm_rules = [r for r in config.rules if any(d != "*" for d in r.domains)]
    http_rules = [r for r in config.rules if any(d == "*" for d in r.domains)]

    # Render VirtualHosts
    mitm_vhs = render_virtual_hosts(mitm_rules)
    http_vhs = render_virtual_hosts(http_rules, cluster="outbound_original_dst_http")

    # Generate Lua code for placeholder rules (combined from all rules)
    all_rules_for_lua = mitm_rules + http_rules
    lua_code = render_lua_code(all_rules_for_lua)
    lua_filter = render_lua_filter_config(lua_code)

    # MITM SNI domains
    sni_domains = extract_sni_domains(config.rules)
    mitm_cert_dir = "/etc/sidecar/certs/mitm-ca"

    result = template

    if sni_domains:
        mitm_sni_match = "[" + ", ".join(f'"{d}"' for d in sni_domains) + "]"
        replacements = {
            "{{SIDECAR_ADMIN_PORT}}": str(args.admin_port),
            "{{SIDECAR_PROXY_PORT}}": str(args.proxy_port),
            "{{MITM_CERT_PATH}}": mitm_cert_dir,
            "{{MITM_SNI_MATCH}}": mitm_sni_match,
            "{{OUTBOUND_VIRTUAL_HOSTS}}": http_vhs,
            "{{OUTBOUND_HTTPS_VIRTUAL_HOSTS}}": mitm_vhs,
        }
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        result = result.replace("{{#MITM_CHAIN_START}}", "")
        result = result.replace("{{#MITM_CHAIN_END}}", "")
    else:
        result = re.sub(
            r"\{\{#MITM_CHAIN_START\}\}.*?\{\{#MITM_CHAIN_END\}\}\n?",
            "",
            result,
            flags=re.DOTALL,
        )
        replacements = {
            "{{SIDECAR_ADMIN_PORT}}": str(args.admin_port),
            "{{SIDECAR_PROXY_PORT}}": str(args.proxy_port),
            "{{MITM_CERT_PATH}}": mitm_cert_dir,
            "{{OUTBOUND_VIRTUAL_HOSTS}}": http_vhs,
        }
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)

    # Insert Lua filter into http_filters (both MITM and HTTP chains)
    result = result.replace("{{LUA_FILTER}}", lua_filter)

    # Write output
    with open(args.output, "w") as f:
        f.write(result)

    # Stats
    total_set = sum(len(r.set_rules) for r in config.rules)
    total_ph = sum(1 for r in config.rules for s in r.set_rules if s.placeholder)
    total_rm = sum(len(r.remove) for r in config.rules)
    print(f"[config-renderer] Rendered Envoy config to {args.output}")
    print(f"[config-renderer]   {len(config.rules)} rules (set={total_set}, remove={total_rm})")
    print(f"[config-renderer]   placeholder rules: {total_ph}")
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