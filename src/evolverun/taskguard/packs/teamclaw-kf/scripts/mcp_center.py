"""
MCP Center Search & Detail

Search MCP services and retrieve detail information from MCP Center API.
Supports both CLI and programmatic usage.

Usage:
    # CLI - search MCP services
    python3 scripts/mcp_center.py search --keyword "知识库"
    python3 scripts/mcp_center.py search --keyword "Dima" --page-size 5 --json

    # CLI - get MCP detail by server_code
    python3 scripts/mcp_center.py detail --server-code "mcp.ant.arkai.dimamcpserver"
    python3 scripts/mcp_center.py detail --server-code "mcp.ant.arkai.dimamcpserver" --json

    # Python API
    from scripts.mcp_center import mcp_search, mcp_detail, MCPSearchResult, MCPDetailResult

    result = mcp_search("知识库")
    for item in result.items:
        print(f"{item.server_name} ({item.server_code}): {item.description}")

    detail = mcp_detail("mcp.ant.arkai.dimamcpserver")
    print(f"Endpoints: {[e.url for e in detail.endpoints]}")
    print(f"Tools: {[t.name for t in detail.tools]}")
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from http_utils import post as _http_post, get as _http_get, RequestException

log = logging.getLogger("teamclaw.mcp_center")

# --- Default Config ---

DEFAULT_APP_KEY = "tlWdqKrTfYruAWhAGIysH7WCz3s6q2MI"
DEFAULT_BASE_URL_PROD = "https://antllmbase-prod-124800013.antgroup-inc.cn"
DEFAULT_BASE_URL_PRE = "https://antllmbase-pre-124800013.antgroup-inc.cn"
DEFAULT_ENV = "prod"
DEFAULT_PAGE_NUM = 1
DEFAULT_PAGE_SIZE = 20
MAX_RETRY_ATTEMPTS = 3
REQUEST_TIMEOUT = 30


def _get_config() -> dict[str, str]:
    """Load config — env vars override constants."""
    return {
        "app_key": os.getenv("MCP_CENTER_APP_KEY", DEFAULT_APP_KEY),
        "base_url_prod": os.getenv("MCP_CENTER_BASE_URL_PROD", DEFAULT_BASE_URL_PROD),
        "base_url_pre": os.getenv("MCP_CENTER_BASE_URL_PRE", DEFAULT_BASE_URL_PRE),
        "env": os.getenv("MCP_CENTER_ENV", DEFAULT_ENV),
    }


def _get_base_url(env: str = DEFAULT_ENV) -> str:
    config = _get_config()
    return config["base_url_prod"] if env == "prod" else config["base_url_pre"]


def _get_headers() -> dict[str, str]:
    config = _get_config()
    return {
        "Content-Type": "application/json",
        "appKey": config["app_key"],
    }


# --- Data Classes ---


@dataclass(frozen=True)
class MCPToolInfo:
    """A single tool exposed by an MCP service."""

    name: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description}


@dataclass(frozen=True)
class MCPEndpointInfo:
    """A single endpoint for an MCP service."""

    url: str
    network_type: str = ""
    transport_protocol: str = ""
    env: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "network_type": self.network_type,
            "transport_protocol": self.transport_protocol,
            "env": self.env,
        }


def _extract_owner_name(owner: Any) -> str:
    """Extract owner display name from API response.

    API returns owner as {"userId": "351881", "userName": "慕冕"}
    or as a plain string in some contexts.
    """
    if isinstance(owner, dict):
        return owner.get("userName", "")
    if isinstance(owner, str):
        return owner
    return ""


@dataclass(frozen=True)
class MCPSearchItem:
    """A single MCP service from search results."""

    server_code: str
    server_name: str
    platform_server_code: str = ""
    host_platform: str = ""
    status: str = ""
    run_mode: str = ""
    access_level: str = ""
    owner: str = ""
    description: str = ""
    category: str = ""
    endpoints: list[MCPEndpointInfo] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_code": self.server_code,
            "server_name": self.server_name,
            "platform_server_code": self.platform_server_code,
            "host_platform": self.host_platform,
            "status": self.status,
            "run_mode": self.run_mode,
            "access_level": self.access_level,
            "owner": self.owner,
            "description": self.description,
            "category": self.category,
            "endpoints": [e.to_dict() for e in self.endpoints],
        }


@dataclass(frozen=True)
class MCPSearchResult:
    """Result from an MCP Center search query."""

    query: str
    items: list[MCPSearchItem] = field(default_factory=list)
    total: int = 0
    page_num: int = 1
    page_size: int = 20
    success: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "total": self.total,
            "page_num": self.page_num,
            "page_size": self.page_size,
            "success": self.success,
            "items": [item.to_dict() for item in self.items],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @property
    def has_results(self) -> bool:
        return len(self.items) > 0

    def summary(self) -> str:
        if not self.items:
            return f"MCP search '{self.query}': no results found"
        lines = [f"MCP search '{self.query}': {len(self.items)}/{self.total} results"]
        for i, item in enumerate(self.items, 1):
            owner = f" (owner: {item.owner})" if item.owner else ""
            lines.append(f"  {i}. [{item.server_code}] {item.server_name}{owner}")
        return "\n".join(lines)


@dataclass(frozen=True)
class MCPDetailResult:
    """Detail information for a single MCP service."""

    server_code: str
    server_name: str
    status: str = ""
    run_mode: str = ""
    access_level: str = ""
    owner: str = ""
    description: str = ""
    category: str = ""
    tools: list[MCPToolInfo] = field(default_factory=list)
    endpoints: list[MCPEndpointInfo] = field(default_factory=list)
    platform_server_code: str = ""
    host_platform: str = ""
    host_app_name: str = ""
    docs: str = ""
    tags: list[str] = field(default_factory=list)
    code_repo_url: str = ""
    success: bool = False
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_code": self.server_code,
            "server_name": self.server_name,
            "status": self.status,
            "run_mode": self.run_mode,
            "access_level": self.access_level,
            "owner": self.owner,
            "description": self.description,
            "category": self.category,
            "tools": [t.to_dict() for t in self.tools],
            "endpoints": [e.to_dict() for e in self.endpoints],
            "platform_server_code": self.platform_server_code,
            "host_platform": self.host_platform,
            "host_app_name": self.host_app_name,
            "docs": self.docs,
            "tags": self.tags,
            "code_repo_url": self.code_repo_url,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def summary(self) -> str:
        if not self.success:
            return f"MCP detail for '{self.server_code}': {self.message}"
        lines = [
            f"MCP Service: {self.server_name} ({self.server_code})",
            f"  Status: {self.status}",
            f"  Access: {self.access_level}",
            f"  Owner: {self.owner or '(unknown)'}",
            f"  Description: {self.description or '(none)'}",
            f"  Endpoints:",
        ]
        for ep in self.endpoints:
            lines.append(
                f"    - [{ep.network_type}/{ep.transport_protocol}/{ep.env}] {ep.url}"
            )
        if not self.endpoints:
            lines.append("    (none)")
        lines.append(f"  Tools ({len(self.tools)}):")
        for t in self.tools:
            desc = f" - {t.description}" if t.description else ""
            lines.append(f"    - {t.name}{desc}")
        if not self.tools:
            lines.append("    (none)")
        if self.docs:
            lines.append(f"  Docs: {self.docs}")
        if self.tags:
            lines.append(f"  Tags: {', '.join(self.tags)}")
        return "\n".join(lines)


# --- Internal API Calls ---


def _query_list(
    *,
    env: str = DEFAULT_ENV,
    page_num: int = DEFAULT_PAGE_NUM,
    page_size: int = DEFAULT_PAGE_SIZE,
    search_key: Optional[str] = None,
    server_codes: Optional[list[str]] = None,
    platform_server_codes: Optional[list[str]] = None,
    statuses: Optional[list[str]] = None,
    transport_protocols: Optional[list[str]] = None,
    host_platforms: Optional[list[str]] = None,
    owners: Optional[list[str]] = None,
    categories: Optional[list[str]] = None,
    network_types: Optional[list[str]] = None,
    run_modes: Optional[list[str]] = None,
    tenants: Optional[list[str]] = None,
) -> Optional[dict[str, Any]]:
    """Call MCP Center search API with retry."""
    url = f"{_get_base_url(env)}/mcp/openapi/mcp/server/search"
    payload: dict[str, Any] = {
        "appKey": _get_config()["app_key"],
        "pageNum": page_num,
        "pageSize": page_size,
    }
    if search_key:
        payload["searchKey"] = search_key
    if server_codes:
        payload["serverCodes"] = server_codes
    if platform_server_codes:
        payload["platformServerCodes"] = platform_server_codes
    if statuses:
        payload["statuses"] = statuses
    if transport_protocols:
        payload["transportProtocols"] = transport_protocols
    if host_platforms:
        payload["hostPlatforms"] = host_platforms
    if owners:
        payload["owners"] = owners
    if categories:
        payload["categories"] = categories
    if network_types:
        payload["networkTypes"] = network_types
    if run_modes:
        payload["runModes"] = run_modes
    if tenants:
        payload["tenants"] = tenants

    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            resp = _http_post(
                url, headers=_get_headers(), json=payload, timeout=REQUEST_TIMEOUT,
            )
            result = resp.json()
            if not result or not result.get("success"):
                raise Exception(f"MCP Center list failed: {result}")
            return result
        except RequestException as e:
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                log.error("[query_list] Failed after %s attempts: %s", MAX_RETRY_ATTEMPTS, e)
                return None
            wait = 2 ** (attempt + 1)
            log.warning("[query_list] Retry in %ss: %s", wait, e)
            time.sleep(wait)
        except Exception as e:
            log.error("[query_list] Non-retryable error: %s", e)
            return None
    return None


def _query_detail(
    platform_server_code: str,
    host_platform: str,
    *,
    env: str = DEFAULT_ENV,
) -> Optional[dict[str, Any]]:
    """Call MCP Center detail API with retry."""
    url = f"{_get_base_url(env)}/mcp/openapi/mcp/server/getByPlatformServerCode"
    params = {
        "hostPlatform": host_platform,
        "platformServerCode": platform_server_code,
    }
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            resp = _http_get(
                url, headers=_get_headers(), params=params, timeout=REQUEST_TIMEOUT,
            )
            result = resp.json()
            if not result or not result.get("success"):
                raise Exception(f"MCP Center detail failed: {result}")
            return result
        except RequestException as e:
            if attempt == MAX_RETRY_ATTEMPTS - 1:
                log.error(
                    "[query_detail] Failed after %s attempts for %s: %s",
                    MAX_RETRY_ATTEMPTS, platform_server_code, e,
                )
                return None
            wait = 2 ** (attempt + 1)
            log.warning("[query_detail] Retry in %ss: %s", wait, e)
            time.sleep(wait)
        except Exception as e:
            log.error("[query_detail] Non-retryable error for %s: %s", platform_server_code, e)
            return None
    return None


def _find_host_platform(server_code: str, *, env: str = DEFAULT_ENV) -> tuple[Optional[str], Optional[str]]:
    """Look up hostPlatform and platformServerCode for a given server_code."""
    result = _query_list(page_num=1, page_size=20, search_key=server_code, env=env)
    if not result or not result.get("success"):
        return None, None
    data = result.get("data", {})
    mcp_list = data.get("data", []) if isinstance(data, dict) else []
    for mcp in mcp_list:
        if mcp.get("serverCode") == server_code:
            return mcp.get("hostPlatform"), mcp.get("platformServerCode")
    return None, None


# --- Parse raw data into dataclasses ---


def _parse_endpoints(raw: dict[str, Any]) -> list[MCPEndpointInfo]:
    """Parse endpoints array from API response.

    API returns: [{"networkType": "OFFICE", "transportProtocol": "SSE",
                   "env": "PROD", "url": "https://..."}]
    """
    endpoints_raw = raw.get("endpoints", [])
    if not isinstance(endpoints_raw, list):
        return []
    return [
        MCPEndpointInfo(
            url=ep.get("url", ""),
            network_type=ep.get("networkType", ""),
            transport_protocol=ep.get("transportProtocol", ""),
            env=ep.get("env", ""),
        )
        for ep in endpoints_raw if isinstance(ep, dict) and ep.get("url")
    ]


def _parse_search_item(raw: dict[str, Any]) -> MCPSearchItem:
    """Parse a raw MCP list item dict into MCPSearchItem.

    Key field mappings (API → dataclass):
        serverCode        → server_code
        name              → server_name
        platformServerCode → platform_server_code
        hostPlatform      → host_platform
        status            → status
        runMode           → run_mode
        accessLevel       → access_level
        owner             → owner (extract userName from {userId, userName})
        description       → description
        category          → category (single string, e.g. "TECH_DEV")
        endpoints         → endpoints (array of {url, networkType, transportProtocol, env})
    """
    return MCPSearchItem(
        server_code=raw.get("serverCode", ""),
        server_name=raw.get("name", ""),
        platform_server_code=raw.get("platformServerCode", ""),
        host_platform=raw.get("hostPlatform", ""),
        status=raw.get("status", ""),
        run_mode=raw.get("runMode", ""),
        access_level=raw.get("accessLevel", ""),
        owner=_extract_owner_name(raw.get("owner")),
        description=raw.get("description", ""),
        category=raw.get("category", ""),
        endpoints=_parse_endpoints(raw),
        raw=raw,
    )


def _parse_detail(raw: dict[str, Any]) -> MCPDetailResult:
    """Parse a raw MCP detail dict into MCPDetailResult.

    Key field mappings (API → dataclass):
        serverCode        → server_code
        name              → server_name
        platformServerCode → platform_server_code
        hostPlatform      → host_platform
        hostAppName       → host_app_name
        status            → status
        runMode           → run_mode
        accessLevel       → access_level
        owner             → owner (extract userName from {userId, userName})
        description       → description
        category          → category
        tools             → tools (array of {name, description})
        endpoints         → endpoints (array of {url, networkType, transportProtocol, env})
        docs              → docs (may be dict or string)
        tags              → tags (array of strings)
        codeRepoUrl       → code_repo_url
    """
    tools_raw = raw.get("tools", [])
    tools = [
        MCPToolInfo(
            name=t.get("name", ""),
            description=t.get("description", ""),
        )
        for t in tools_raw if isinstance(t, dict)
    ]

    # docs can be a dict {"overview": "..."} or a plain string
    docs_raw = raw.get("docs", "")
    docs = ""
    if isinstance(docs_raw, dict):
        docs = docs_raw.get("overview", "")
    elif isinstance(docs_raw, str):
        docs = docs_raw

    tags = raw.get("tags", [])
    if not isinstance(tags, list):
        tags = []

    server_code = raw.get("serverCode", "")
    server_name = raw.get("name", "")

    return MCPDetailResult(
        server_code=server_code,
        server_name=server_name,
        status=raw.get("status", ""),
        run_mode=raw.get("runMode", ""),
        access_level=raw.get("accessLevel", ""),
        owner=_extract_owner_name(raw.get("owner")),
        description=raw.get("description", ""),
        category=raw.get("category", ""),
        tools=tools,
        endpoints=_parse_endpoints(raw),
        platform_server_code=raw.get("platformServerCode", ""),
        host_platform=raw.get("hostPlatform", ""),
        host_app_name=raw.get("hostAppName", ""),
        docs=docs,
        tags=tags,
        code_repo_url=raw.get("codeRepoUrl", ""),
        success=bool(server_code),
        raw=raw,
    )


# --- Public API ---


def mcp_search(
    keyword: str = "",
    *,
    page_num: int = DEFAULT_PAGE_NUM,
    page_size: int = DEFAULT_PAGE_SIZE,
    server_codes: Optional[list[str]] = None,
    platform_server_codes: Optional[list[str]] = None,
    statuses: Optional[list[str]] = None,
    transport_protocols: Optional[list[str]] = None,
    host_platforms: Optional[list[str]] = None,
    owners: Optional[list[str]] = None,
    categories: Optional[list[str]] = None,
    network_types: Optional[list[str]] = None,
    run_modes: Optional[list[str]] = None,
    tenants: Optional[list[str]] = None,
    env: str = DEFAULT_ENV,
) -> MCPSearchResult:
    """Search MCP services from MCP Center.

    Args:
        keyword: Search keyword (fuzzy match on name/code/description).
        page_num: Page number (1-based).
        page_size: Results per page.
        server_codes: Filter by server codes.
        platform_server_codes: Filter by platform server codes.
        statuses: Filter by statuses (e.g. ["ONLINE"]).
        transport_protocols: Filter by transport protocols.
        host_platforms: Filter by host platforms.
        owners: Filter by owners.
        categories: Filter by categories.
        network_types: Filter by network types.
        run_modes: Filter by run modes.
        tenants: Filter by tenants.
        env: "prod" or "pre".

    Returns:
        MCPSearchResult with matched items.
    """
    result = _query_list(
        env=env,
        page_num=page_num,
        page_size=page_size,
        search_key=keyword or None,
        server_codes=server_codes,
        platform_server_codes=platform_server_codes,
        statuses=statuses,
        transport_protocols=transport_protocols,
        host_platforms=host_platforms,
        owners=owners,
        categories=categories,
        network_types=network_types,
        run_modes=run_modes,
        tenants=tenants,
    )
    if not result or not result.get("success"):
        msg = result.get("resultMsg", "MCP Center request failed") if result else "No response"
        return MCPSearchResult(
            query=keyword, success=False, message=msg,
        )
    data = result.get("data", {})
    mcp_list = data.get("data", []) if isinstance(data, dict) else []
    items = [_parse_search_item(m) for m in mcp_list if isinstance(m, dict)]
    return MCPSearchResult(
        query=keyword,
        items=items,
        total=data.get("total", 0) if isinstance(data, dict) else 0,
        page_num=data.get("pageNum", page_num) if isinstance(data, dict) else page_num,
        page_size=data.get("pageSize", page_size) if isinstance(data, dict) else page_size,
        success=True,
    )


def mcp_detail(
    server_code: str,
    *,
    env: str = DEFAULT_ENV,
) -> MCPDetailResult:
    """Get detail information for an MCP service by server_code.

    This first searches for the server_code to resolve host_platform and
    platform_server_code, then fetches the full detail.

    Args:
        server_code: The MCP server code (e.g. "mcp.ant.arkai.dimamcpserver").
        env: "prod" or "pre".

    Returns:
        MCPDetailResult with service detail, tools, and connection info.
    """
    host_platform, platform_server_code = _find_host_platform(server_code, env=env)
    if not host_platform or not platform_server_code:
        return MCPDetailResult(
            server_code=server_code,
            server_name="",
            success=False,
            message=f"Cannot find host_platform or platform_server_code for '{server_code}'",
        )
    result = _query_detail(platform_server_code, host_platform, env=env)
    if not result:
        return MCPDetailResult(
            server_code=server_code,
            server_name="",
            success=False,
            message=f"No detail result for '{server_code}'",
        )
    if not result.get("success"):
        return MCPDetailResult(
            server_code=server_code,
            server_name="",
            success=False,
            message=f"MCP Center error: retCode={result.get('retCode')}, msg={result.get('resultMsg')}",
        )
    mcp_data = result.get("data", {})
    if not mcp_data:
        return MCPDetailResult(
            server_code=server_code,
            server_name="",
            success=False,
            message=f"Empty detail data for '{server_code}'",
        )
    return _parse_detail(mcp_data)


# --- CLI ---


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="MCP Center search & detail")
    sub = parser.add_subparsers(dest="command", required=True)

    # --- search subcommand ---
    search_parser = sub.add_parser("search", help="Search MCP services")
    search_parser.add_argument("--keyword", "-k", default="", help="Search keyword (fuzzy match)")
    search_parser.add_argument("--page-num", type=int, default=DEFAULT_PAGE_NUM, help="Page number")
    search_parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Results per page")
    search_parser.add_argument("--status", nargs="*", help="Filter by status (e.g. ONLINE)")
    search_parser.add_argument("--env", choices=["prod", "pre"], default=DEFAULT_ENV, help="Environment")
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # --- detail subcommand ---
    detail_parser = sub.add_parser("detail", help="Get MCP service detail")
    detail_parser.add_argument("--server-code", "-s", required=True, help="MCP server code")
    detail_parser.add_argument("--env", choices=["prod", "pre"], default=DEFAULT_ENV, help="Environment")
    detail_parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.command == "search":
        result = mcp_search(
            keyword=args.keyword,
            page_num=args.page_num,
            page_size=args.page_size,
            statuses=args.status,
            env=args.env,
        )
        print(result.to_json() if args.json else result.summary())

    elif args.command == "detail":
        result = mcp_detail(args.server_code, env=args.env)
        print(result.to_json() if args.json else result.summary())


if __name__ == "__main__":
    main()