#!/usr/bin/env python3
"""
config-renderer: 将 header-rules.yaml 渲染为 Envoy 静态配置

只处理出站流量, 操作 request header
两种操作: set (存在替换/不存在添加) + remove (删除)
按域名匹配 -> VirtualHost, 每个域名一组 header 规则
配置了域名的规则自动支持 MITM (HTTPS 解密), SNI 域名从 rules 自动推导

支持的值前缀:
  enc:      XOR 加密值 (base64 编码), 渲染时自动解密
  其他:     原样使用
"""

import argparse
import base64
import sys
from itertools import cycle
from typing import Any

import yaml


# ---- 常量 ----

# 默认 XOR 加解密密钥 (与 Rust 端 header_inject.rs 的 FIXED_DECRYPTION_KEY 一致)
# 优先从 header-rules.yaml 的 crypto_key 字段读取, 未配置时使用此默认值
DEFAULT_CRYPTO_KEY = "091415a7ee1c3808392f88d6d17cdaed8720029bfaef5eb3ddeb178df49fb0eb"

ENC_PREFIX = "enc:"


# ---- 数据结构 ----

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


# ---- 加解密 ----

def xor_encrypt(plaintext: str, key: str) -> str:
    """XOR 加密并 base64 编码, 与 Rust 端 decrypt_value 互逆"""
    key_bytes = key.encode("utf-8")
    plain_bytes = plaintext.encode("utf-8")
    encrypted = bytes(a ^ b for a, b in zip(plain_bytes, cycle(key_bytes)))
    return base64.b64encode(encrypted).decode("utf-8")


def xor_decrypt(encrypted_b64: str, key: str) -> str | None:
    """XOR 解密 (与 Rust 端 decrypt_value 一致)"""
    try:
        decoded = base64.b64decode(encrypted_b64)
        key_bytes = key.encode("utf-8")
        decrypted = bytes(a ^ b for a, b in zip(decoded, cycle(key_bytes)))
        return decrypted.decode("utf-8")
    except Exception:
        return None


# ---- 解析 ----

def resolve_value(value: str, crypto_key: str) -> str:
    """解析值前缀, 返回实际值:
    - enc:XXX   -> XOR 解密 (使用 crypto_key)
    - 其他       -> 原样返回
    """
    if value.startswith(ENC_PREFIX):
        encrypted = value[len(ENC_PREFIX):]
        decrypted = xor_decrypt(encrypted, crypto_key)
        if decrypted is not None:
            return decrypted
        print(f"WARNING: failed to decrypt enc: value, using original value", file=sys.stderr)
        return value

    return value


def parse_config(data: dict[str, Any]) -> HeaderRulesConfig:
    """解析 header-rules.yaml"""
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


# ---- 渲染 ----

def escape_yaml_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_headers_to_add(rules: list[SetRule], indent: str, crypto_key: str) -> str:
    """渲染 request_headers_to_add (set = OVERWRITE_IF_EXISTS_OR_ADD)

    indent 是列表项 "-" 所在的缩进层级, 内部子属性相对缩进:
      {indent}- header:
      {indent}    key: "..."
      {indent}    value: "..."
      {indent}  append_action: OVERWRITE_IF_EXISTS_OR_ADD
    """
    if not rules:
        return "[]"
    items = []
    for r in rules:
        resolved = resolve_value(r.value, crypto_key)
        items.append(
            f'{indent}- header:\n'
            f'{indent}    key: "{escape_yaml_string(r.header)}"\n'
            f'{indent}    value: "{escape_yaml_string(resolved)}"\n'
            f'{indent}  append_action: OVERWRITE_IF_EXISTS_OR_ADD'
        )
    return "\n" + "\n".join(items)


def render_headers_to_remove(rules: list[str], indent: str) -> str:
    """渲染 request_headers_to_remove"""
    if not rules:
        return "[]"
    items = [f'{indent}- "{escape_yaml_string(r)}"' for r in rules]
    return "\n" + "\n".join(items)


def render_virtual_host(rule: OutboundRule, idx: int, crypto_key: str) -> str:
    """渲染单个 VirtualHost

    缩进层级 (virtual_hosts 下的列表项):
      {item}  - name: xxx              # 列表项 dash
      {prop}    domains:               # 列表项内属性 (与 name 对齐)
      {child}     - "example.com"      # 属性下的子列表
      {prop}    routes:
      {child}     - match: ...
      {prop}    request_headers_to_add:
      {child}     - header: ...
    """
    item = "                    "   # 20 spaces: 列表项 "-" 的缩进
    prop = item + "  "             # 22 spaces: 列表项内属性 (name, domains, routes, request_headers_to_add)
    child = prop + "  "            # 24 spaces: 属性下的子列表项

    vh_name = rule.name or f"outbound_rule_{idx}"

    # domains
    domains_yaml = "\n".join(f'{child}- "{d}"' for d in rule.domains)

    # 单个兜底路由
    routes_yaml = (
        f'{child}- match:\n'
        f'{child}    prefix: "/"\n'
        f'{child}  route:\n'
        f'{child}    cluster: outbound_original_dst\n'
        f'{child}    timeout: 0s'
    )

    # header 操作
    header_parts = []
    req_add = render_headers_to_add(rule.set_rules, child, crypto_key)
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


def render_virtual_hosts(rules: list[OutboundRule], crypto_key: str) -> str:
    """渲染所有 VirtualHosts"""
    if not rules:
        return "[]"
    return "\n" + "\n".join(render_virtual_host(r, i, crypto_key) for i, r in enumerate(rules))


def extract_sni_domains(rules: list[OutboundRule]) -> list[str]:
    """从规则中提取 MITM SNI 域名 (排除通配符 '*')"""
    domains = set()
    for rule in rules:
        for d in rule.domains:
            if d != "*":
                domains.add(d)
    return sorted(domains)


# ---- 主流程 ----

def cmd_render(args):
    """渲染 Envoy 配置"""
    # 读取模板
    try:
        with open(args.template, "r") as f:
            template = f.read()
    except FileNotFoundError:
        print(f"ERROR: failed to read template {args.template}", file=sys.stderr)
        sys.exit(1)

    # 读取规则
    try:
        with open(args.rules, "r") as f:
            rules_data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        print(f"WARNING: failed to read rules {args.rules}, using defaults", file=sys.stderr)
        rules_data = {}
    except yaml.YAMLError as e:
        print(f"ERROR: failed to parse rules: {e}", file=sys.stderr)
        sys.exit(1)

    # 读取 crypto_key (优先从配置文件, 未配置则使用默认值)
    crypto_key = rules_data.get("crypto_key", DEFAULT_CRYPTO_KEY)

    config = parse_config(rules_data)

    # 渲染 VirtualHosts
    outbound_vhs = render_virtual_hosts(config.rules, crypto_key)

    # MITM SNI 域名: 从规则自动推导 (排除 catch-all '*')
    sni_domains = extract_sni_domains(config.rules)
    if sni_domains:
        mitm_sni_match = "[" + ", ".join(f'"{d}"' for d in sni_domains) + "]"
    else:
        mitm_sni_match = "[]"

    mitm_cert_dir = "/etc/sidecar/certs/mitm-ca"

    # 替换占位符
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

    # 写入输出
    with open(args.output, "w") as f:
        f.write(result)

    # 统计
    total_set = sum(len(r.set_rules) for r in config.rules)
    total_rm = sum(len(r.remove) for r in config.rules)
    print(f"[config-renderer] Rendered Envoy config to {args.output}")
    print(f"[config-renderer]   {len(config.rules)} rules (set={total_set}, remove={total_rm})")
    print(f"[config-renderer]   MITM SNI domains: {sni_domains}")


def cmd_encrypt(args):
    """加密一个字符串, 输出 enc: 前缀的密文"""
    crypto_key = _get_crypto_key(args)
    encrypted = xor_encrypt(args.plaintext, crypto_key)
    print(f"enc:{encrypted}")


def cmd_decrypt(args):
    """解密一个 enc: 前缀的密文, 验证用"""
    crypto_key = _get_crypto_key(args)
    ciphertext = args.ciphertext
    if ciphertext.startswith(ENC_PREFIX):
        ciphertext = ciphertext[len(ENC_PREFIX):]
    result = xor_decrypt(ciphertext, crypto_key)
    if result is not None:
        print(result)
    else:
        print("ERROR: decryption failed", file=sys.stderr)
        sys.exit(1)


def _get_crypto_key(args) -> str:
    """从配置文件读取 crypto_key, 未配置则使用默认值"""
    rules_path = getattr(args, "rules", None)
    if rules_path:
        try:
            with open(rules_path, "r") as f:
                data = yaml.safe_load(f) or {}
            return data.get("crypto_key", DEFAULT_CRYPTO_KEY)
        except Exception:
            pass
    return DEFAULT_CRYPTO_KEY


def main():
    parser = argparse.ArgumentParser(description="Envoy config renderer and value encryptor")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 默认子命令: render (向后兼容, 无子命令时执行 render)
    parser.add_argument("--template", default="/etc/envoy/envoy-template.yaml", help=argparse.SUPPRESS)
    parser.add_argument("--rules", default="/etc/sidecar/header-rules.yaml", help=argparse.SUPPRESS)
    parser.add_argument("--output", default="/etc/envoy/envoy.yaml", help=argparse.SUPPRESS)
    parser.add_argument("--proxy-port", type=int, default=38080, help=argparse.SUPPRESS)
    parser.add_argument("--admin-port", type=int, default=38081, help=argparse.SUPPRESS)

    # render 子命令
    render_parser = subparsers.add_parser("render", help="Render Envoy config from header-rules.yaml")
    render_parser.add_argument("--template", default="/etc/envoy/envoy-template.yaml", help="Envoy config template path")
    render_parser.add_argument("--rules", default="/etc/sidecar/header-rules.yaml", help="Header rules YAML path")
    render_parser.add_argument("--output", default="/etc/envoy/envoy.yaml", help="Output config path")
    render_parser.add_argument("--proxy-port", type=int, default=38080, help="Outbound proxy port")
    render_parser.add_argument("--admin-port", type=int, default=38081, help="Envoy admin port")
    render_parser.set_defaults(func=cmd_render)

    # encrypt 子命令
    encrypt_parser = subparsers.add_parser("encrypt", help="Encrypt a plaintext string (output: enc:BASE64)")
    encrypt_parser.add_argument("plaintext", help="Plaintext string to encrypt")
    encrypt_parser.add_argument("--rules", default="/etc/sidecar/header-rules.yaml", help="Header rules YAML path (reads crypto_key from)")
    encrypt_parser.set_defaults(func=cmd_encrypt)

    # decrypt 子命令
    decrypt_parser = subparsers.add_parser("decrypt", help="Decrypt an enc:BASE64 ciphertext")
    decrypt_parser.add_argument("ciphertext", help="Ciphertext to decrypt (with or without enc: prefix)")
    decrypt_parser.add_argument("--rules", default="/etc/sidecar/header-rules.yaml", help="Header rules YAML path (reads crypto_key from)")
    decrypt_parser.set_defaults(func=cmd_decrypt)

    args = parser.parse_args()

    if args.command is None:
        # 向后兼容: 无子命令时执行 render
        cmd_render(args)
    elif hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
