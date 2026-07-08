"""AICoding workspace service — workspace management, environment check, git clone.

工作区路径: {bolt_base}/{entity_type}_{entity_id}/{bot_id}/aicoding{path}
path 必须以 /workspace 开头
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, Optional

import httpx
from injector import inject

from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.plugin_api.sandbox_runtime import RELAY_PORT, SandboxRuntimeClient

log = logging.getLogger("aicoding-workspace")

CLONE_TIMEOUT = 300.0

# 语言检测映射
_LANGUAGE_MARKERS = {
    "TypeScript": ["tsconfig.json", "package.json"],
    "JavaScript": ["package.json", ".js"],
    "Python": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"],
    "Go": ["go.mod", "go.sum"],
    "Rust": ["Cargo.toml"],
    "Java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    "C++": ["CMakeLists.txt", "Makefile"],
}


class WorkspaceService:
    """工作区管理服务

    工作区路径: {bolt_base}/{entity_type}_{entity_id}/{bot_id}/aicoding{path}
    path 由前端传入，必须以 /workspace 开头
    """

    @inject
    def __init__(
        self,
        bot_provider: BotService,
        device_provider: DeviceService,
        path_factory: WorkspacePathFactory,
        sandbox_client: SandboxRuntimeClient,
    ) -> None:
        self._bot_provider = bot_provider
        self._device_provider = device_provider
        self._path_factory = path_factory
        self._sandbox_client = sandbox_client

    def get_workspace_path(
        self,
        entity_id: str,
        bot_id: str,
        relative_path: str = "/workspace",
        entity_type: str = "staff",
    ) -> str:
        """获取工作区路径

        Args:
            entity_id: 用户/团队/项目 ID
            bot_id: Bot ID
            relative_path: 相对路径，如 /workspace 或 /workspace/myproject
            entity_type: 实体类型 (staff/team/project)

        Returns:
            工作区绝对路径
        """
        # 获取 aicoding 引擎的根目录
        bot_engine_dir = self._path_factory.get_bot_engine_dir(
            entity_id, bot_id, "aicoding", entity_type
        )
        # relative_path 以 / 开头，去掉开头的 / 拼接
        # 例如: /workspace -> workspace
        relative_path = relative_path.lstrip("/")
        workspace_path = bot_engine_dir / relative_path
        return str(workspace_path)

    async def initialize_workspace(
        self,
        user_id: str,
        bot_id: str,
        path: str = "/workspace",
        entity_type: str = "staff",
        git_url: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        """初始化工作区

        Args:
            user_id: 用户 ID
            bot_id: Bot ID
            path: 工作区相对路径，必须以 /workspace 开头
            entity_type: 实体类型
            git_url: 可选的 Git 仓库 URL
            branch: 可选的 Git 分支

        Returns:
            初始化结果，包含 workspace_id, path, name, repos, git, warnings, ready
        """
        # 获取工作区路径
        workspace_path = self.get_workspace_path(user_id, bot_id, path, entity_type)

        # 如果提供了 git_url，执行克隆
        if git_url:
            # 从 URL 推导目标目录
            repo_name = git_url.rstrip("/").split("/")[-1]
            if repo_name.endswith(".git"):
                repo_name = repo_name[:-4]
            target_dir = os.path.join(workspace_path, repo_name)

            # 检查目标目录是否已存在
            if os.path.exists(target_dir):
                return {
                    "workspace_id": "",
                    "path": target_dir,
                    "name": repo_name,
                    "repos": [],
                    "git": None,
                    "warnings": [f"目标目录已存在: {target_dir}"],
                    "ready": False,
                    "error": f"目标目录已存在: {target_dir}",
                }

            # 确保工作区目录存在
            os.makedirs(workspace_path, exist_ok=True)

            # 执行克隆
            try:
                await self._clone_repository(git_url, target_dir, branch, bot_id, user_id)
                workspace_path = target_dir
            except Exception as e:
                return {
                    "workspace_id": "",
                    "path": workspace_path,
                    "name": "",
                    "repos": [],
                    "git": None,
                    "warnings": [],
                    "ready": False,
                    "error": f"克隆失败: {str(e)}",
                }
        else:
            # 确保工作区目录存在
            if not os.path.exists(workspace_path):
                try:
                    os.makedirs(workspace_path, exist_ok=True)
                except Exception as e:
                    return {
                        "workspace_id": "",
                        "path": workspace_path,
                        "name": "",
                        "repos": [],
                        "git": None,
                        "warnings": [],
                        "ready": False,
                        "error": f"创建目录失败: {str(e)}",
                    }

        # 执行环境检查
        return await self._check_workspace(workspace_path, user_id)

    async def _clone_repository(
        self,
        git_url: str,
        target_dir: str,
        branch: Optional[str] = None,
        bot_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """通过 relay 容器执行 Git 克隆"""
        if not self._bot_provider or not self._device_provider:
            raise RuntimeError("WorkspaceService requires bot_provider and device_provider for git clone")

        sandbox_id = self._resolve_sandbox_id(bot_id, user_id)
        req = self._sandbox_client.build_proxy_request(
            sandbox_id=sandbox_id, api_path="/api/git/clone", port=RELAY_PORT
        )

        async with httpx.AsyncClient(timeout=CLONE_TIMEOUT) as client:
            resp = await client.post(
                req.url,
                json={"url": git_url, "target_dir": target_dir, "branch": branch},
                headers=req.headers,
            )
            resp.raise_for_status()
            result = resp.json()

        if not result.get("success"):
            raise Exception(result.get("error", "克隆失败"))

    def _resolve_sandbox_id(self, bot_id: Optional[str], user_id: Optional[str]) -> str:
        """bot_id → binding_id → sandbox_id"""
        bot = self._bot_provider.get_bot(bot_id, user_id)
        binding_id = bot.get("binding_id")
        if not binding_id:
            raise ValueError(f"Bot {bot_id} has no device binding")

        device = self._device_provider.get_device(binding_id=binding_id)
        device_props = getattr(device, "device_props", {}) or {}
        sandbox_id = device_props.get("sandbox_id")
        if not sandbox_id:
            raise ValueError(f"Bot {bot_id} device has no sandbox_id")
        return sandbox_id

    async def _check_workspace(self, path: str, user_id: str) -> Dict[str, Any]:
        """检查工作区环境"""
        warnings = []
        repos = []
        git_info = None

        # 路径校验
        if not os.path.isabs(path):
            return {
                "workspace_id": "",
                "path": path,
                "name": "",
                "repos": [],
                "git": None,
                "warnings": ["路径必须是绝对路径"],
                "ready": False,
            }

        if not os.path.isdir(path):
            return {
                "workspace_id": "",
                "path": path,
                "name": "",
                "repos": [],
                "git": None,
                "warnings": [f"目录不存在: {path}"],
                "ready": False,
            }

        # 检测语言
        detected_languages = []
        for lang, markers in _LANGUAGE_MARKERS.items():
            for marker in markers:
                if os.path.exists(os.path.join(path, marker)):
                    detected_languages.append(lang)
                    break

        if detected_languages:
            primary_lang = detected_languages[0]
            repos.append(
                {
                    "language": primary_lang,
                    "path": ".",
                    "dependency_status": _check_deps(path, primary_lang),
                }
            )

        # 检测 Git 状态
        git_info = await _get_git_info(path)

        if not git_info:
            warnings.append("目录不是 Git 仓库")

        # 生成 workspace_id（基于路径的简单哈希）
        workspace_id = f"ws-{uuid.uuid4().hex[:8]}"
        workspace_name = os.path.basename(path) or path

        return {
            "workspace_id": workspace_id,
            "path": path,
            "name": workspace_name,
            "repos": repos,
            "git": git_info,
            "warnings": warnings,
            "ready": True,
        }


# ── 内部辅助函数 ──────────────────────────────────────────────────────────


def _check_deps(path: str, language: str) -> str:
    """检测依赖安装状态"""
    if language in ("TypeScript", "JavaScript"):
        node_modules = os.path.join(path, "node_modules")
        return "installed" if os.path.isdir(node_modules) else "missing"
    if language == "Python":
        venv = os.path.join(path, ".venv") or os.path.join(path, "venv")
        if os.path.isdir(venv):
            return "installed"
        return "unknown"
    return "unknown"


async def _get_git_info(path: str) -> Optional[Dict[str, str]]:
    """获取 Git 状态信息"""
    git_dir = os.path.join(path, ".git")
    if not os.path.isdir(git_dir):
        return None

    result: Dict[str, str] = {}

    try:
        # 获取分支
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--abbrev-ref", "HEAD",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            result["branch"] = stdout.decode().strip()

        # 获取 remote URL
        proc = await asyncio.create_subprocess_exec(
            "git", "remote", "get-url", "origin",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            result["remote_url"] = stdout.decode().strip()

        # 获取工作区状态
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        if proc.returncode == 0:
            lines = stdout.decode().strip().split("\n")
            changes = [line for line in lines if line.strip()]
            result["status"] = f"{len(changes)} changes" if changes else "clean"

    except Exception as e:
        log.warning(f"Git info check failed for {path}: {e}")

    return result if result else None
