#!/usr/bin/env python3
"""
Dima 缺陷创建脚本

通过 Dima OpenAPI 创建缺陷（Bug）。创建需求（Issue）请使用 MCP 工具 mcp__Dima-MCP__createIssue。
基于 Project OpenApi 文档: https://yuque.antfin.com/dima/gs1zsi/ht81n00i138e83o5

安全说明：
- 优先从已编译的安全模块（_secrets.so）获取密钥
- 其次从环境变量 DIMA_ACCESS_KEY / DIMA_SECRET_KEY 获取
- 禁止在代码中硬编码密钥
- 支持从 .env 文件加载配置

Usage:
    # 创建缺陷（主要用途）
    python dima_create_bug.py bug \\
        --subject "前端页面白屏" \\
        --module 前端 \\
        --processor-id "012345" \\
        --staff-id "010001" \\
        --priority high \\
        --description "用户反馈打开某页面白屏"

    # Dry-run（仅打印参数）
    python dima_create_bug.py bug \\
        --subject "测试" \\
        --module 后端 \\
        --processor-id "012345" \\
        --dry-run
"""

import os
import sys
import json
import argparse
import hashlib
import time
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from urllib.parse import quote

from http_utils import get as _http_get, post as _http_post, put as _http_put, RequestException

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    _AES_AVAILABLE = True
except ImportError:
    _AES_AVAILABLE = False

try:
    from dotenv import load_dotenv
    _DOTENV_AVAILABLE = True
except ImportError:
    _DOTENV_AVAILABLE = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class WorkItemCategory(str, Enum):
    REQ = "Req"
    BUG = "Bug"
    TASK = "Task"
    PROD_ISSUE = "ProdIssue"


class Priority(str, Enum):
    URGENT = "94"
    HIGH = "95"
    MEDIUM = "96"
    LOW = "97"


PRIORITY_MAP = {
    "urgent": Priority.URGENT.value,
    "high": Priority.HIGH.value,
    "medium": Priority.MEDIUM.value,
    "low": Priority.LOW.value,
}

# TeamClaw 固定 workspaceId
DEFAULT_WORKSPACE_ID = "W26001113566"

# 生产环境 API
BASE_URL = "https://devapi.alipay.com/arkcooprod/openapi"

# 模块 -> 负责人工号映射（可按需补充）
MODULE_PROCESSORS: dict[str, list[str]] = {
    "前端": ["与白", "敛秋"],
    "后端": ["江绅", "墨馠", "安远", "斩秋"],
    "Adaptor": ["涔涔", "一朴"],
    "openclaw引擎": ["卓人", "元歌", "楚生"],
    "BCN": ["卓人", "元歌"],
    "系统工程": ["萧辚", "毅舒", "温悦"],
    "安全权限与隐私": ["允川"],
    "智能能力": ["文汐", "楚生", "山宗", "墨馠"],
    "产品建议": ["阡秋", "川漠"],
    "协作功能": ["卓人"],
    "VSCode插件": ["澜起"],
    "本地客户端": ["毅舒"],
}

# 模块 -> 默认优先级
MODULE_DEFAULT_PRIORITY: dict[str, str] = {
    "前端": "high",
    "后端": "high",
    "Adaptor": "high",
    "openclaw引擎": "high",
    "BCN": "high",
    "系统工程": "medium",
    "安全权限与隐私": "urgent",
    "智能能力": "medium",
    "产品建议": "medium",
    "协作功能": "medium",
    "VSCode插件": "medium",
    "本地客户端": "medium",
}

# DIMA 自定义字段 ID
FIELD_MODULE = "FIELD2023001001071"          # 所属模块
FIELD_SCENARIO_LABEL = "FIELD2024001001689"  # 场景标签
FIELD_TAG = "FIELD2023001000003"             # 标签

# 默认场景标签（线上用户 Bug）
DEFAULT_SCENARIO_LABELS = ["业务测试", "回归托管"]

# 默认标签 ID（线上用户 Bug）
DEFAULT_TAG_IDS = ["26001060999", "26001060997"]

# 模块名 → DIMA 所属模块可选值映射
MODULE_FIELD_VALUES: dict[str, list[str]] = {
    "前端": ["前端"],
    "后端": ["后端"],
    "Adaptor": ["Adaptor"],
    "openclaw引擎": ["openclaw引擎"],
    "BCN": ["BCN"],
    "系统工程": ["系统工程"],
    "安全权限与隐私": ["安全权限与隐私"],
    "智能能力": ["智能能力"],
    "产品建议": ["产品建议"],
    "协作功能": ["协作功能"],
    "VSCode插件": ["VSCode插件"],
    "本地客户端": ["本地客户端"],
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DimaConfig:
    access_key: str
    secret_key: str
    tenant: str = "alipay"
    base_url: str = BASE_URL
    workspace_id: str = DEFAULT_WORKSPACE_ID
    staff_id: str = ""


def _load_secrets_from_compiled_module() -> Optional[tuple[str, str]]:
    """Try to load keys from compiled _secrets.so module (most secure).

    The _secrets.so module (from my_skills/secure) exposes get_dima_config()
    which returns {"access_key": ..., "secret_key": ...}.
    """
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, script_dir)
        from _secrets import get_dima_config
        config = get_dima_config()
        if config and config.get("access_key") and config.get("secret_key"):
            return config["access_key"], config["secret_key"]
    except (ImportError, AttributeError, TypeError):
        pass
    return None


def _load_config() -> DimaConfig:
    """Load Dima config from secrets module, env vars, or .env file."""
    # Try .env file
    if _DOTENV_AVAILABLE:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        load_dotenv(env_path, override=False)

    # Try compiled secrets module first
    secrets = _load_secrets_from_compiled_module()

    access_key = (secrets[0] if secrets else "") or os.environ.get("DIMA_ACCESS_KEY", "")
    secret_key = (secrets[1] if secrets else "") or os.environ.get("DIMA_SECRET_KEY", "")

    if not access_key or not secret_key:
        print(
            "错误: 未找到 Dima API 密钥。请设置环境变量 DIMA_ACCESS_KEY 和 DIMA_SECRET_KEY，\n"
            "或在 .env 文件中配置，或编译 _secrets.so 模块。",
            file=sys.stderr,
        )
        sys.exit(1)

    return DimaConfig(
        access_key=access_key,
        secret_key=secret_key,
        tenant=os.environ.get("DIMA_TENANT", "alipay"),
        base_url=os.environ.get("DIMA_BASE_URL", BASE_URL),
        workspace_id=os.environ.get("DIMA_WORKSPACE_ID", DEFAULT_WORKSPACE_ID),
        staff_id=os.environ.get("DIMA_STAFF_ID", ""),
    )


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------

def _generate_signature(access_key: str, secret_key: str, timestamp: int) -> str:
    """Generate AES-ECB signature for Dima OpenAPI request.

    Signature algorithm (per Dima OpenApi spec):
    1. Build sign string: accessKey={accessKey}&timestamp={timestamp}
    2. Encrypt with AES-ECB using secret_key (must be 16 chars)
    3. Return uppercase hex of ciphertext
    """
    if not _AES_AVAILABLE:
        print("错误: 请安装 pycryptodome: pip install pycryptodome", file=sys.stderr)
        sys.exit(1)

    if len(secret_key) != 16:
        print(f"错误: SecretKey 长度必须为 16，当前为 {len(secret_key)}", file=sys.stderr)
        sys.exit(1)

    sign_str = f"accessKey={access_key}&timestamp={timestamp}".encode("utf-8")
    key = secret_key.encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted_data = cipher.encrypt(pad(sign_str, AES.block_size))
    return encrypted_data.hex().upper()


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------

class DimaClient:
    """Low-level Dima OpenAPI client."""

    def __init__(self, config: DimaConfig) -> None:
        self._config = config

    def _request(
        self,
        method: str,
        path: str,
        query_params: Optional[dict[str, str]] = None,
        body: Optional[dict] = None,
        staff_id: Optional[str] = None,
    ) -> dict:
        params = dict(query_params or {})
        sid = staff_id or self._config.staff_id
        if sid:
            params["staffId"] = sid

        timestamp = int(time.time() * 1000)
        trace_id = os.environ.get("DIMA_TRACE_ID", "") or uuid.uuid4().hex + uuid.uuid4().hex[:8]

        body_str = json.dumps(body, ensure_ascii=False) if body else ""
        signature = _generate_signature(
            access_key=self._config.access_key,
            secret_key=self._config.secret_key,
            timestamp=timestamp,
        )

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "AccessKey": self._config.access_key,
            "Signature": signature,
            "Timestamp": str(timestamp),
            "ARK_OPENAPI_TENANT": self._config.tenant,
            "ARK_OPENAPI_TRACE": trace_id,
        }

        url = f"{self._config.base_url}{path}"

        if method.upper() == "GET":
            resp = _http_get(url, params=params, headers=headers, timeout=30)
        elif method.upper() == "POST":
            resp = _http_post(url, params=params, data=body_str, headers=headers, timeout=30)
        elif method.upper() == "PUT":
            resp = _http_put(url, params=params, data=body_str, headers=headers, timeout=30)
        else:
            raise ValueError(f"Unsupported method: {method}")

        # Handle empty response body (e.g. auth errors return empty body)
        if not resp.text.strip():
            memo = resp.headers.get("Memo", "")
            result_status = resp.headers.get("Result-Status", "")
            raise RuntimeError(
                f"API 返回空响应: Result-Status={result_status}, Memo={memo}, "
                f"URL={url}, Method={method}"
            )

        result = resp.json()

        if not result.get("success", False):
            print(
                f"API 请求失败: code={result.get('code')}, message={result.get('message')}",
                file=sys.stderr,
            )

        return result

    def get_user_id(self, staff_id: str) -> Optional[str]:
        """Get Dima userId from staffId."""
        result = self._request("GET", "/common/user/getUserId", staff_id=staff_id)
        if result.get("success"):
            return result.get("data")
        return None

    def create_work_item(
        self,
        *,
        staff_id: str,
        workspace_id: str,
        work_item_category: str,
        subject: str,
        processor_id: str,
        content: Optional[str] = None,
        priority_id: Optional[str] = None,
        project_id: Optional[str] = None,
        work_item_type_id: Optional[str] = None,
        template_type_name: Optional[str] = None,
        out_system: Optional[str] = None,
        out_biz_no: Optional[str] = None,
        field_values: Optional[list[dict]] = None,
    ) -> dict:
        """Create a work item (bug/req/task) via Dima OpenAPI.

        Args:
            staff_id: Creator staff ID (补0工号)
            workspace_id: Workspace ID
            work_item_category: "Req" | "Bug" | "Task" | "ProdIssue"
            subject: Work item title
            processor_id: Processor staff ID (补0工号)
            content: Work item description (rich text)
            priority_id: "94"(紧急) | "95"(高) | "96"(中) | "97"(低)
            project_id: Optional project ID
            work_item_type_id: Optional work item type ID
            template_type_name: Optional template type (e.g. "线上用户") to bypass required field validation
            out_system: Optional source system name
            out_biz_no: Optional source business ID
            field_values: Optional list of field values for workItemFieldValueList

        Returns:
            API response dict. On success, data contains the work item ID.
        """
        body: dict = {
            "workspaceId": workspace_id,
            "workItemCategory": work_item_category,
            "subject": subject,
            "processorId": processor_id,
        }

        if project_id:
            body["projectId"] = project_id
        if priority_id:
            body["priorityId"] = priority_id
        if work_item_type_id:
            body["workItemTypeId"] = work_item_type_id
        if template_type_name:
            body["templateTypeName"] = template_type_name
        if out_system:
            body["outSystem"] = out_system
        if out_biz_no:
            body["outBizNo"] = out_biz_no
        if content:
            body["workItemDocument"] = {
                "formatType": "MARKDOWN",
                "editorType": "YUQUE",
                "content": content,
            }
        if field_values:
            # Create API uses customFieldValueSimpleParamList with:
            #   customFieldId + customFieldValueList (array)
            # Update API uses workItemFieldValueList with:
            #   workItemFieldIdentity + workItemFieldValueList (array)
            body["customFieldValueSimpleParamList"] = [
                {
                    "customFieldId": fv["workItemFieldIdentity"],
                    "customFieldValueList": fv["workItemFieldValueList"],
                }
                for fv in field_values
            ]

        return self._request(
            "POST",
            "/workItem/create",
            body=body,
            staff_id=staff_id,
        )

    def update_work_item(
        self,
        *,
        staff_id: str,
        work_item_id: str,
        field_values: list[dict],
    ) -> dict:
        """Update work item fields.

        Args:
            staff_id: Modifier staff ID
            work_item_id: Work item ID
            field_values: List of {workItemFieldIdentity, workItemFieldValueList}
        """
        body = {
            "workItemId": work_item_id,
            "workItemFieldValueList": field_values,
        }
        return self._request(
            "POST",
            "/workItem/update",
            body=body,
            staff_id=staff_id,
        )

    def update_work_item_document(
        self,
        *,
        staff_id: str,
        work_item_id: str,
        content: str,
    ) -> dict:
        """Update work item description.

        Args:
            staff_id: Modifier staff ID
            work_item_id: Work item ID
            content: New description content
        """
        body = {
            "content": content,
            "formatType": "MARKDOWN",
            "editorType": "YUQUE",
        }
        return self._request(
            "POST",
            f"/workItem/document/update",
            body=body,
            query_params={"workItemId": work_item_id},
            staff_id=staff_id,
        )

    def search_work_items(
        self,
        *,
        staff_id: str,
        target_type: str = "WORKSPACE",
        target_id: str = "",
        belong: str = "Bug",
        scope: str = "all",
        page: int = 1,
        page_size: int = 20,
        simple_user_mode: bool = True,
        simple_field_mode: bool = True,
        simple_filter: Optional[list[dict]] = None,
    ) -> dict:
        """Search work items.

        Args:
            staff_id: Querier staff ID
            target_type: "WORKSPACE" | "PROJECT"
            target_id: Workspace ID or Project ID
            belong: "Req" | "Bug" | "Task" | "Workitem" | "ProdIssue"
            scope: "all" | "associate" | "" (empty = owned only)
            page: Page number
            page_size: Page size (max 500)
            simple_user_mode: Return simplified user info
            simple_field_mode: Return simplified field info
            simple_filter: Filter conditions
        """
        body: dict = {
            "targetType": target_type,
            "targetId": target_id,
            "belong": belong,
            "scope": scope,
            "page": page,
            "pageSize": page_size,
            "simpleUserMode": simple_user_mode,
            "simpleFieldMode": simple_field_mode,
        }
        if simple_filter:
            body["simpleFilter"] = simple_filter

        return self._request(
            "POST",
            "/workItem/search",
            body=body,
            staff_id=staff_id,
        )

    def add_tracker(
        self,
        *,
        staff_id: str,
        work_item_id: str,
        tracker_staff_ids: list[str],
    ) -> dict:
        """Add followers to a work item.

        Args:
            staff_id: Operator staff ID
            work_item_id: Work item ID
            tracker_staff_ids: List of staff IDs to add as followers
        """
        body = {
            "workItemId": work_item_id,
            "trackerStaffIdList": tracker_staff_ids,
        }
        return self._request(
            "POST",
            "/workItem/tracker/add",
            body=body,
            staff_id=staff_id,
        )


# ---------------------------------------------------------------------------
# High-level TeamClaw helpers
# ---------------------------------------------------------------------------

def _resolve_processor(module: str) -> str:
    """Resolve module name to first processor name."""
    if not module:
        return ""
    for key, names in MODULE_PROCESSORS.items():
        if key.lower() == module.lower() or module.lower() in key.lower():
            return names[0]
    return ""


def _build_bug_content(
    *,
    reporter_id: str = "",
    reporter_name: str = "",
    module: str = "",
    processor: str = "",
    description: str = "",
    frontend_log: str = "",
    backend_log: str = "",
    adaptor_log: str = "",
    openclaw_log: str = "",
    langfuse_info: str = "",
) -> str:
    """Build Bug content from the TeamClaw template (Markdown format)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections = [
        "### 【问题上报人】",
        f"- 工号：{reporter_id}",
        f"- 花名：{reporter_name}",
        f"- 上报时间：{now}",
        "",
        "### 【问题模块】",
        f"- 所属模块：{module}",
        f"- 负责人：{processor}",
        "",
        "### 【问题描述】",
        description or "（未提供）",
        "",
        "### 【错误现场】",
        "",
        "**前端日志**",
        f"> 查询参数：应用=open-claw，时间=近30分钟，关键字={reporter_id or reporter_name}",
        "",
        "关键错误：",
        frontend_log or "（未查询）",
        "",
        "**后端日志**",
        f"> 查询参数：应用=agentclaw，日志=start.log，时间=近30分钟，关键字={reporter_id}",
        "",
        "关键错误：",
        backend_log or "（未查询）",
        "",
        "**Adaptor日志**",
        f"> 查询参数：应用=arcaagentclaw，日志=adaptor_err.log，时间=近30分钟，关键字=（仅machine_name）",
        "",
        "关键错误：",
        adaptor_log or "（未查询）",
        "",
        "**OpenClaw日志**",
        f"> 查询参数：应用=arcaagentclaw，日志=openclaw_err.log，时间=近30分钟，关键字=（仅machine_name）",
        "",
        "关键错误：",
        openclaw_log or "（未查询）",
    ]

    if langfuse_info:
        sections.append("")
        sections.append("### 【Langfuse对话记录】")
        sections.append(langfuse_info)

    return "\n".join(sections)


def _build_issue_content(
    *,
    reporter_id: str = "",
    reporter_name: str = "",
    module: str = "",
    processor: str = "",
    description: str = "",
    expected: str = "",
    scenario: str = "",
) -> str:
    """Build Issue (requirement) content from the TeamClaw template."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections = [
        "### 【需求提出人】",
        f"- 工号：{reporter_id}",
        f"- 花名：{reporter_name}",
        f"- 提出时间：{now}",
        "",
        "### 【需求模块】",
        f"- 所属模块：{module}",
        f"- 负责人：{processor}",
        "",
        "### 【需求描述】",
        description or "（未提供）",
        "",
        "### 【预期效果】",
        expected or "（未提供）",
        "",
        "### 【使用场景】",
        scenario or "（未提供）",
    ]
    return "\n".join(sections)


def create_teamclaw_bug(
    client: DimaClient,
    *,
    staff_id: str,
    subject: str,
    module: str,
    processor: str = "",
    processor_id: str = "",
    reporter_id: str = "",
    reporter_name: str = "",
    priority: str = "high",
    content: str = "",
    description: str = "",
    frontend_log: str = "",
    backend_log: str = "",
    adaptor_log: str = "",
    openclaw_log: str = "",
    langfuse_info: str = "",
) -> dict:
    """Create a TeamClaw bug via Dima OpenAPI.

    This is the primary high-level function for creating TeamClaw bugs.
    It handles template formatting, module-to-processor resolution,
    and priority mapping automatically.

    Args:
        client: DimaClient instance
        staff_id: Creator staff ID (补0工号)
        subject: Bug title (prefix 【线上用户】 will be added if missing)
        module: Module name (前端/后端/Adaptor/BCN/系统工程/安全权限与隐私/智能能力/产品建议/...)
        processor: Processor name (resolved from module if empty)
        processor_id: Processor staff ID (required, must be 补0工号)
        reporter_id: Reporter staff ID
        reporter_name: Reporter nickname
        priority: "urgent" | "high" | "medium" | "low"
        content: Pre-formatted content (if provided, template is skipped)
        description: Bug description (used in template if content is empty)
        frontend_log: Frontend log findings
        backend_log: Backend log findings
        adaptor_log: Adaptor log findings
        openclaw_log: OpenClaw log findings
        langfuse_info: Langfuse session info

    Returns:
        API response dict with work item ID on success.
    """
    if not subject.startswith("【线上用户】"):
        subject = f"【线上用户】{subject}"

    resolved_processor = processor or _resolve_processor(module)
    priority_id = PRIORITY_MAP.get(priority, Priority.HIGH.value)

    if content:
        formatted_content = content
    else:
        formatted_content = _build_bug_content(
            reporter_id=reporter_id,
            reporter_name=reporter_name,
            module=module,
            processor=resolved_processor,
            description=description,
            frontend_log=frontend_log,
            backend_log=backend_log,
            adaptor_log=adaptor_log,
            openclaw_log=openclaw_log,
            langfuse_info=langfuse_info,
        )

    if not processor_id:
        print(
            "错误: 必须指定 processor_id（处理人工号，补0）。"
            "可通过 --processor-id 参数或 DIMA_PROCESSOR_ID 环境变量设置。",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build custom field values: 所属模块 + 场景标签 + 标签
    # Create API uses customFieldValueSimpleParamList with:
    #   customFieldId + customFieldValueList (mapped from workItemFieldIdentity + workItemFieldValueList)
    field_values: list[dict] = []
    if module:
        module_values = MODULE_FIELD_VALUES.get(module, [module])
        field_values.append({
            "workItemFieldIdentity": FIELD_MODULE,
            "workItemFieldValueList": module_values,
        })
    field_values.append({
        "workItemFieldIdentity": FIELD_SCENARIO_LABEL,
        "workItemFieldValueList": DEFAULT_SCENARIO_LABELS,
    })
    field_values.append({
        "workItemFieldIdentity": FIELD_TAG,
        "workItemFieldValueList": DEFAULT_TAG_IDS,
    })

    result = client.create_work_item(
        staff_id=staff_id,
        workspace_id=client._config.workspace_id,
        work_item_category=WorkItemCategory.BUG.value,
        subject=subject,
        processor_id=processor_id,
        content=formatted_content,
        priority_id=priority_id,
        template_type_name="线上用户",
        field_values=field_values,
    )

    return result


def create_teamclaw_issue(
    client: DimaClient,
    *,
    staff_id: str,
    subject: str,
    module: str,
    processor: str = "",
    processor_id: str = "",
    reporter_id: str = "",
    reporter_name: str = "",
    content: str = "",
    description: str = "",
    expected: str = "",
    scenario: str = "",
) -> dict:
    """Create a TeamClaw issue (requirement) via Dima OpenAPI."""
    if not subject.startswith("【线上用户】"):
        subject = f"【线上用户】{subject}"

    resolved_processor = processor or _resolve_processor(module)

    if content:
        formatted_content = content
    else:
        formatted_content = _build_issue_content(
            reporter_id=reporter_id,
            reporter_name=reporter_name,
            module=module,
            processor=resolved_processor,
            description=description,
            expected=expected,
            scenario=scenario,
        )

    if not processor_id:
        print(
            "错误: 必须指定 processor_id（处理人工号，补0）。",
            file=sys.stderr,
        )
        sys.exit(1)

    return client.create_work_item(
        staff_id=staff_id,
        workspace_id=client._config.workspace_id,
        work_item_category=WorkItemCategory.REQ.value,
        subject=subject,
        processor_id=processor_id,
        content=formatted_content,
        priority_id=Priority.MEDIUM.value,
    )


def create_teamclaw_task(
    client: DimaClient,
    *,
    staff_id: str,
    subject: str,
    processor_id: str,
    content: str = "",
    priority: str = "medium",
) -> dict:
    """Create a TeamClaw task via Dima OpenAPI."""
    priority_id = PRIORITY_MAP.get(priority, Priority.MEDIUM.value)

    return client.create_work_item(
        staff_id=staff_id,
        workspace_id=client._config.workspace_id,
        work_item_category=WorkItemCategory.TASK.value,
        subject=subject,
        processor_id=processor_id,
        content=content or None,
        priority_id=priority_id,
    )


def format_work_item_url(work_item_id: str) -> str:
    """Format the TeamClaw work item URL."""
    return f"https://project.alipay.com/workItem?workItemId={work_item_id}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dima 缺陷创建脚本 (TeamClaw) — 需求请使用 MCP createIssue",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 创建缺陷
  python dima_create_bug.py bug \\
      --subject "前端页面白屏" \\
      --module 前端 \\
      --processor-id 012345 \\
      --priority high \\
      --description "用户打开页面时白屏"

  # 使用预格式化内容
  python dima_create_bug.py bug \\
      --subject "XXX报错" \\
      --module 后端 \\
      --processor-id 012345 \\
      --content-file bug_content.txt
""",
    )

    sub = parser.add_subparsers(dest="category", help="工作项类别")
    sub.required = True

    for cat_name, cat_help in [
        ("bug", "创建缺陷 (Bug)"),
        ("issue", "创建需求 (Req)"),
        ("task", "创建任务 (Task)"),
    ]:
        sp = sub.add_parser(cat_name, help=cat_help)
        _add_common_args(sp)
        if cat_name == "bug":
            _add_bug_args(sp)
        elif cat_name == "issue":
            _add_issue_args(sp)

    return parser


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments common to all categories."""
    parser.add_argument("--subject", required=True, help="工作项标题")
    parser.add_argument("--processor-id", help="处理人工号（补0）")
    parser.add_argument("--processor", help="处理人花名（用于模板）")
    parser.add_argument("--module", default="", help="所属模块")
    parser.add_argument("--priority", default="medium", choices=list(PRIORITY_MAP.keys()), help="优先级")
    parser.add_argument("--staff-id", default="", help="创建人工号（补0），默认从 DIMA_STAFF_ID 环境变量读取")
    parser.add_argument("--reporter-id", default="", help="上报人工号")
    parser.add_argument("--reporter-name", default="", help="上报人花名")
    parser.add_argument("--workspace-id", default="", help="空间ID（默认 W26001113566）")
    parser.add_argument("--project-id", default="", help="项目ID（可选）")
    parser.add_argument("--content", default="", help="预格式化内容（如果提供则跳过模板）")
    parser.add_argument("--content-file", default="", help="从文件读取内容")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON 格式输出")
    parser.add_argument("--dry-run", action="store_true", help="仅打印请求参数，不实际调用 API")


def _add_bug_args(parser: argparse.ArgumentParser) -> None:
    """Add bug-specific arguments."""
    parser.add_argument("--description", default="", help="问题描述")
    parser.add_argument("--frontend-log", default="", help="前端日志关键错误")
    parser.add_argument("--backend-log", default="", help="后端日志关键错误")
    parser.add_argument("--adaptor-log", default="", help="Adaptor日志关键错误")
    parser.add_argument("--openclaw-log", default="", help="OpenClaw日志关键错误")
    parser.add_argument("--langfuse-info", default="", help="Langfuse对话记录摘要")


def _add_issue_args(parser: argparse.ArgumentParser) -> None:
    """Add issue-specific arguments."""
    parser.add_argument("--description", default="", help="需求描述")
    parser.add_argument("--expected", default="", help="预期效果")
    parser.add_argument("--scenario", default="", help="使用场景")


def _main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    config = _load_config()

    # Override config with CLI args
    effective_workspace_id = args.workspace_id or config.workspace_id
    effective_staff_id = args.staff_id or config.staff_id

    if args.workspace_id or args.staff_id:
        config = DimaConfig(
            access_key=config.access_key,
            secret_key=config.secret_key,
            tenant=config.tenant,
            base_url=config.base_url,
            workspace_id=effective_workspace_id,
            staff_id=effective_staff_id,
        )

    staff_id = effective_staff_id
    if not staff_id:
        print("错误: 请通过 --staff-id 参数或 DIMA_STAFF_ID 环境变量指定创建人工号", file=sys.stderr)
        sys.exit(1)

    processor_id = args.processor_id or os.environ.get("DIMA_PROCESSOR_ID", "")
    if not processor_id:
        print("错误: 请通过 --processor-id 参数指定处理人工号（补0）", file=sys.stderr)
        sys.exit(1)

    content = args.content
    if args.content_file:
        with open(args.content_file, "r", encoding="utf-8") as f:
            content = f.read()

    category_map = {
        "bug": WorkItemCategory.BUG.value,
        "issue": WorkItemCategory.REQ.value,
        "task": WorkItemCategory.TASK.value,
    }
    work_item_category = category_map[args.category]

    # Auto-prefix subject
    subject = args.subject
    if work_item_category in (WorkItemCategory.BUG.value, WorkItemCategory.REQ.value):
        if not subject.startswith("【线上用户】"):
            subject = f"【线上用户】{subject}"

    # Build content based on category
    if content:
        formatted_content = content
    elif args.category == "bug":
        formatted_content = _build_bug_content(
            reporter_id=args.reporter_id,
            reporter_name=args.reporter_name,
            module=args.module,
            processor=args.processor or _resolve_processor(args.module),
            description=args.description,
            frontend_log=args.frontend_log,
            backend_log=args.backend_log,
            adaptor_log=args.adaptor_log,
            openclaw_log=args.openclaw_log,
            langfuse_info=args.langfuse_info,
        )
    elif args.category == "issue":
        formatted_content = _build_issue_content(
            reporter_id=args.reporter_id,
            reporter_name=args.reporter_name,
            module=args.module,
            processor=args.processor or _resolve_processor(args.module),
            description=args.description,
            expected=getattr(args, "expected", ""),
            scenario=getattr(args, "scenario", ""),
        )
    else:
        formatted_content = ""

    priority_id = PRIORITY_MAP.get(args.priority, Priority.MEDIUM.value)

    # Build templateTypeName for Bug (use "线上用户" template)
    template_type_name: str | None = None
    if work_item_category == WorkItemCategory.BUG.value:
        template_type_name = "线上用户"

    # Build field_values for Bug (所属模块 + 场景标签 + 标签)
    field_values: list[dict] | None = None
    if work_item_category == WorkItemCategory.BUG.value:
        field_values = []
        if args.module:
            module_values = MODULE_FIELD_VALUES.get(args.module, [args.module])
            field_values.append({
                "workItemFieldIdentity": FIELD_MODULE,
                "workItemFieldValueList": module_values,
            })
        field_values.append({
            "workItemFieldIdentity": FIELD_SCENARIO_LABEL,
            "workItemFieldValueList": DEFAULT_SCENARIO_LABELS,
        })
        field_values.append({
            "workItemFieldIdentity": FIELD_TAG,
            "workItemFieldValueList": DEFAULT_TAG_IDS,
        })

    # Dry run
    if args.dry_run:
        dry_run_info = {
            "category": work_item_category,
            "workspaceId": config.workspace_id,
            "subject": subject,
            "processorId": processor_id,
            "priorityId": priority_id,
            "staffId": staff_id,
            "content": formatted_content,
        }
        if args.module:
            dry_run_info["module"] = args.module
        if args.project_id:
            dry_run_info["projectId"] = args.project_id
        if template_type_name:
            dry_run_info["templateTypeName"] = template_type_name
        if field_values:
            dry_run_info["fieldValues"] = field_values

        print(json.dumps(dry_run_info, ensure_ascii=False, indent=2))
        return

    # Create client and submit
    client = DimaClient(config)

    result = client.create_work_item(
        staff_id=staff_id,
        workspace_id=config.workspace_id,
        work_item_category=work_item_category,
        subject=subject,
        processor_id=processor_id,
        content=formatted_content,
        priority_id=priority_id,
        project_id=args.project_id or None,
        template_type_name=template_type_name,
        field_values=field_values,
    )

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("success"):
        work_item_id = result.get("data", "")
        url = format_work_item_url(work_item_id)
        category_label = {"Bug": "缺陷", "Req": "需求", "Task": "任务"}.get(work_item_category, "工作项")
        print(f"✅ {category_label}已提交：")
        print(f"📌 编号：{work_item_id}")
        print(f"🔗 链接：{url}")
    else:
        print(f"❌ 创建失败: {result.get('message', '未知错误')}", file=sys.stderr)
        if not args.json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _main()