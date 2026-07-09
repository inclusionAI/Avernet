"""
DIMA OpenAPI client for creating workspaces and managing work items.
"""
import json
import time
import random
import string
from typing import Dict, Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from injector import inject

from agentclaw.community.log import get_logger
from agentclaw.community.di.config import WorkspaceHostingConfig
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


def do_sign(access_key: str, access_secret: str, timestamp: int) -> str:
    """Generate signature using AES-128-ECB encryption."""
    if len(access_secret) != 16:
        raise ValueError(f"Access Secret must be exactly 16 characters, got {len(access_secret)}")

    sign_str = f"accessKey={access_key}&timestamp={timestamp}"
    key = access_secret.encode('utf-8')
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted_data = cipher.encrypt(pad(sign_str.encode('utf-8'), AES.block_size))
    return encrypted_data.hex().upper()


class WorkspaceHostingClient:
    """DIMA OpenAPI client with AK/SK authentication.

    Dependencies are injected via constructor:
        - WorkspaceHostingConfig: Configuration provider
    """

    @inject
    def __init__(self, config: WorkspaceHostingConfig):
        """Initialize DIMA client with injected configuration.

        Args:
            config: DIMA configuration (injected via DI)
        """
        self._config = config
        self.base_url = config.base_url.rstrip('/')
        self.access_key = config.access_key
        self.access_secret = config.access_secret
        self.tenant = config.tenant
        self.timeout = config.timeout

        # 根据环境选择 aixcore base URL：预发用 aixcore_base_url_pre，线上用 aixcore_base_url
        current_env = get_current_env()
        if current_env == "prod":
            self.aixcore_base_url = config.aixcore_base_url.rstrip('/')
        else:
            self.aixcore_base_url = config.aixcore_base_url_pre.rstrip('/')

        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _make_request(
        self,
        method: str,
        path: str,
        staff_id: str,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        allow_empty_data: bool = False,
    ) -> Dict[str, Any]:
        """Make HTTP request to DIMA API."""
        url = f"{self.base_url}{path}"
        timestamp_ms = int(time.time() * 1000)

        try:
            signature = do_sign(self.access_key, self.access_secret, timestamp_ms)
        except Exception as e:
            logger.error(f"[workspace_hosting_client] Failed to generate signature: {e}")
            raise

        trace_id = ''.join(random.choices(string.hexdigits[:16], k=32))

        headers = {
            "AccessKey": self.access_key,
            "Signature": signature,
            "Timestamp": str(timestamp_ms),
            "ARK_OPENAPI_TENANT": self.tenant,
            "ARK_OPENAPI_TRACE": trace_id,
        }

        if data is not None:
            headers["Content-Type"] = "application/json"

        try:
            request_data = json.dumps(data, ensure_ascii=False) if data is not None else None
            logger.info(f"[workspace_hosting_client] {method} {url} staffId={staff_id}")

            response = self.session.request(
                method=method,
                url=url,
                data=request_data,
                params=params,
                headers=headers,
                timeout=self.timeout,
                verify=False,
            )

            logger.info(f"[workspace_hosting_client] Response status: {response.status_code}")

            try:
                result = response.json()
            except Exception as e:
                logger.error(f"[workspace_hosting_client] Failed to parse JSON: {e}, text: {response.text[:500]}")
                raise

            if not isinstance(result, dict):
                try:
                    result = json.loads(result) if isinstance(result, str) else {}
                except Exception:
                    raise Exception(f"API returned invalid format: {type(result)}, expected dict")

            if not result.get("success"):
                error_code = result.get("code", "UNKNOWN")
                error_msg = result.get("message", result.get("errorMsg", "Unknown error"))
                raise Exception(f"DIMA API error [{error_code}]: {error_msg}")

            if response.status_code >= 400:
                raise Exception(f"HTTP {response.status_code}: {response.text}")

            if result.get("data") is None and not allow_empty_data:
                raise Exception("DIMA API returned no data")

            # DIMA API 有时返回的 data 字段是 JSON 字符串而非 dict，需要二次解析
            data_val = result.get("data")
            if isinstance(data_val, str):
                try:
                    result["data"] = json.loads(data_val)
                except (json.JSONDecodeError, TypeError):
                    pass

            return result

        except requests.exceptions.RequestException as e:
            raise Exception(f"Request failed: {str(e)}")

    def query_staff_department(self, work_no: str) -> Optional[str]:
        """Query staff department ID via aixcore API.

        Args:
            work_no: 用户工号

        Returns:
            部门 ID (deptNo)，查询失败时返回 None
        """
        url = f"{self.aixcore_base_url}/api/staffInfo/queryEmpInfo"
        data = {"workNo": work_no}

        try:
            logger.info(f"[workspace_hosting_client] Querying staff department: work_no={work_no}")

            response = self.session.request(
                method="POST",
                url=url,
                json=data,
                timeout=self.timeout,
                verify=False,
            )

            result = response.json()

            if not result.get("success"):
                error_code = result.get("errorCode", "UNKNOWN")
                error_msg = result.get("errorMsg", "Unknown error")
                logger.warning(
                    f"[workspace_hosting_client] Failed to query staff department for {work_no}: "
                    f"[{error_code}] {error_msg}"
                )
                return None

            emp_info = result.get("data", {})
            if isinstance(emp_info, str):
                try:
                    emp_info = json.loads(emp_info)
                except (json.JSONDecodeError, TypeError):
                    emp_info = {}

            dept_no = emp_info.get("deptNo") if isinstance(emp_info, dict) else None
            if dept_no:
                logger.info(
                    f"[workspace_hosting_client] Staff department found: work_no={work_no}, dept_no={dept_no}"
                )
            else:
                logger.warning(
                    f"[workspace_hosting_client] No deptNo found in response for work_no={work_no}"
                )

            return dept_no

        except Exception as e:
            logger.error(
                f"[workspace_hosting_client] Failed to query staff department for {work_no}: {e}",
                exc_info=True,
            )
            return None

    def create_workspace(
        self,
        staff_id: str,
        workspace_name: str,
        department_id: str = "52146",
        workspace_desc: str = "",
        template_id: str = "SPACEFORM2025001000121",
        public_level: str = "ALL",
        logo: str = "icon-kongjian3",
    ) -> Dict[str, Any]:
        """Create a new DIMA workspace.

        Args:
            staff_id: 用户工号
            workspace_name: 空间名称
            department_id: 部门 ID，默认 F4858
            workspace_desc: 空间描述
            template_id: 空间模板 ID
            public_level: 公开级别 ALL/TEAM/PRIVATE
            logo: 空间图标标识
        """
        logger.info(f"[workspace_hosting_client] Creating workspace: name={workspace_name}, staff_id={staff_id}, dept={department_id}")

        path = "/arkcooprod/openapi/workspace/create"
        params = {"staffId": staff_id}
        data = {
            "name": workspace_name,
            "description": workspace_desc,
            "departmentId": department_id,
            "logo": logo,
            "publicLevel": public_level,
            "memberUserIds": [staff_id],
            "templateId": template_id,
        }

        try:
            result = self._make_request("POST", path, staff_id, params=params, data=data)

            if isinstance(result, str):
                result = json.loads(result)

            # 防御性处理：data 字段可能是字符串而非 dict
            ws_data = result.get("data", {}) if isinstance(result, dict) else {}
            if isinstance(ws_data, str):
                try:
                    ws_data = json.loads(ws_data)
                    result["data"] = ws_data
                except (json.JSONDecodeError, TypeError):
                    ws_data = {}

            workspace_id = ws_data.get("workspaceId") if isinstance(ws_data, dict) else None
            logger.info(f"[workspace_hosting_client] Workspace created: id={workspace_id}, name={workspace_name}")
            return result
        except Exception as e:
            logger.error(f"[workspace_hosting_client] Failed to create workspace '{workspace_name}': {e}")
            raise

    def create_work_item(
        self,
        staff_id: str,
        request_body: Dict[str, Any],
    ) -> Dict[str, Any]:
        """创建 DIMA 工作项（透传模式）。

        Args:
            staff_id: 用户工号
            request_body: DIMA API 请求体（包含 workspaceId, subject 等）

        Returns:
            DIMA API 响应
        """
        logger.info(
            f"[workspace_hosting_client] Creating work item: staff_id={staff_id}, "
            f"body_keys={list(request_body.keys())}"
        )

        path = "/arkcooprod/openapi/workItem/create"
        params = {"staffId": staff_id}

        result = self._make_request("POST", path, staff_id, data=request_body, params=params)
        return result

    def create_work_item_relation(
        self,
        operator: str,
        request_body: Dict[str, Any],
    ) -> Dict[str, Any]:
        """创建 DIMA 工作项关联关系（透传模式）。

        Args:
            operator: 操作人工号
            request_body: DIMA API 请求体（包含 relationIdentifier, sourceIdentifier 等）

        Returns:
            DIMA API 响应
        """
        logger.info(
            f"[workspace_hosting_client] Creating work item relation: operator={operator}, "
            f"body_keys={list(request_body.keys())}"
        )

        path = "/arkcooprod/openapi/workItem/relation/record/create"
        params = {"operator": operator}

        result = self._make_request(
            "POST", path, operator, data=request_body, params=params,
            allow_empty_data=True,
        )
        return result

    def upload_file_to_arkgw(
        self,
        staff_id: str,
        source_id: str,
        file_content: bytes | None = None,
        file_name: str | None = None,
        content_type: str = "application/octet-stream",
        url: str | None = None,
    ) -> Dict[str, Any]:
        """上传文件到 Arkgw（multipart/form-data）。

        file_content/url 二选一。

        Args:
            staff_id: 工号（补0）
            source_id: 调用方系统名
            file_content: 文件内容字节
            file_name: 文件名
            content_type: 文件 MIME 类型
            url: 图片文件链接（转存场景）

        Returns:
            Arkgw API 响应 dict
        """
        path = "/arkgw/openapi/fileManager/file/upload"
        api_url = f"{self.base_url}{path}"
        timestamp_ms = int(time.time() * 1000)

        signature = do_sign(self.access_key, self.access_secret, timestamp_ms)
        trace_id = ''.join(random.choices(string.hexdigits[:16], k=32))

        headers = {
            "AccessKey": self.access_key,
            "Signature": signature,
            "Timestamp": str(timestamp_ms),
            "ARK_OPENAPI_TENANT": self.tenant,
            "ARK_OPENAPI_TRACE": trace_id,
        }

        form_data: Dict[str, Any] = {
            "staffId": staff_id,
            "sourceId": source_id,
        }
        if url:
            form_data["url"] = url

        files = None
        if file_content and file_name:
            files = {"file": (file_name, file_content, content_type)}

        logger.info(
            "[workspace_hosting_client] upload_file_to_arkgw: staffId=%s, sourceId=%s, "
            "fileName=%s, url=%s",
            staff_id, source_id, file_name, url,
        )

        try:
            response = self.session.post(
                url=api_url,
                data=form_data,
                files=files,
                headers=headers,
                timeout=self.timeout,
                verify=False,
            )

            logger.info(f"[workspace_hosting_client] arkgw upload response status: {response.status_code}")

            result = response.json()
            if not isinstance(result, dict):
                raise Exception(f"Arkgw returned invalid format: {type(result)}")

            return result

        except requests.exceptions.RequestException as e:
            raise Exception(f"Arkgw upload request failed: {str(e)}")

    def update_work_item_document(
        self,
        staff_id: str,
        work_item_id: str,
        content: str,
        format_type: str = "MARKDOWN",
        editor_type: str = "YUQUE",
    ) -> Dict[str, Any]:
        """更新 DIMA 工作项描述内容。

        Args:
            staff_id: 用户工号
            work_item_id: 工作项 ID
            content: 工作项描述内容
            format_type: 文档格式 RICHTEXT | MARKDOWN，默认 RICHTEXT
            editor_type: 编辑器类型，默认 YUQUE

        Returns:
            DIMA API 响应
        """
        logger.info(
            "[dima_client] Updating work item document: staff_id=%s, workItemId=%s, formatType=%s",
            staff_id, work_item_id, format_type,
        )

        path = "/arkcooprod/openapi/workItem/document/update"
        params = {
            "staffId": staff_id,
            "workItemId": work_item_id,
        }
        data = {
            "content": content,
            "formatType": format_type,
            "editorType": editor_type,
        }

        result = self._make_request("POST", path, staff_id, data=data, params=params, allow_empty_data=True)
        return result
