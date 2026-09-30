"""Strict configuration binding for platform-managed MCP Headers."""

from __future__ import annotations

from typing import Any

from injector import Module, provider, singleton

from agentclaw.community.di import config as cfg
from agentclaw.community.di.modules import config_module


def _block() -> dict[str, Any]:
    user_config = config_module.read_user_config()
    if "mcp_runtime_credentials" not in user_config:
        return {}
    raw = user_config["mcp_runtime_credentials"]
    if not isinstance(raw, dict):
        raise ValueError("mcp_runtime_credentials must be a mapping")
    unknown = sorted(set(raw) - {"header_secrets"})
    if unknown:
        raise ValueError(
            "mcp_runtime_credentials contains unknown keys: "
            + ", ".join(str(key) for key in unknown)
        )
    return raw


def _parse_header_secrets(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        raise ValueError("mcp_runtime_credentials.header_secrets must be a mapping")

    parsed: dict[str, dict[str, str]] = {}
    for server_code, headers in raw.items():
        if not isinstance(server_code, str) or not server_code.strip():
            raise ValueError(
                "mcp_runtime_credentials.header_secrets contains an empty server_code"
            )
        if server_code != server_code.strip():
            raise ValueError(
                "mcp_runtime_credentials.header_secrets server_code must not "
                "contain surrounding whitespace"
            )
        if not isinstance(headers, dict) or not headers:
            raise ValueError(
                f"mcp_runtime_credentials.header_secrets.{server_code} must be a "
                "non-empty mapping"
            )
        parsed[server_code] = _parse_server_headers(server_code, headers)
    return parsed


def _parse_server_headers(
    server_code: str, headers: dict[Any, Any]
) -> dict[str, str]:
    parsed: dict[str, str] = {}
    normalized_names: set[str] = set()
    for header_name, secret_name in headers.items():
        if not isinstance(header_name, str) or not header_name.strip():
            raise ValueError(
                f"mcp_runtime_credentials.header_secrets.{server_code} contains "
                "an empty header name"
            )
        if header_name != header_name.strip():
            raise ValueError(
                f"mcp_runtime_credentials.header_secrets.{server_code} Header names "
                "must not contain surrounding whitespace"
            )
        normalized_header = header_name.lower()
        if normalized_header in normalized_names:
            raise ValueError(
                f"mcp_runtime_credentials.header_secrets.{server_code} contains "
                f"duplicate case-insensitive Header name {header_name!r}"
            )
        if not isinstance(secret_name, str) or not secret_name.strip():
            raise ValueError(
                f"mcp_runtime_credentials.header_secrets.{server_code}."
                f"{header_name} must name a secret"
            )
        if secret_name != secret_name.strip():
            raise ValueError(
                f"mcp_runtime_credentials.header_secrets.{server_code}."
                f"{header_name} secret name must not contain surrounding whitespace"
            )
        normalized_names.add(normalized_header)
        parsed[header_name] = secret_name
    return parsed


class McpRuntimeCredentialsConfigModule(Module):
    """Bind validated secret references without reading the secret backend."""

    @singleton
    @provider
    def mcp_runtime_credentials(self) -> cfg.McpRuntimeCredentialsConfig:
        block = _block()
        return cfg.McpRuntimeCredentialsConfig(
            header_secrets=_parse_header_secrets(block.get("header_secrets", {}))
        )


__all__ = ["McpRuntimeCredentialsConfigModule"]
