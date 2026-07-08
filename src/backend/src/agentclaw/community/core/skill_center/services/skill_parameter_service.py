"""
技能参数管理服务 - 通过 DeviceFileSystem 读写文件

存储位置：SKILL_PARAMETERS_FILE_PATH（容器内固定路径）
通过 DeviceFileSystem 读写，自动适配 local/arca
"""
import json
from datetime import datetime
from typing import Any

from agentclaw.community.log import get_logger


logger = get_logger()

# 容器内的参数文件路径 (用于 Arca 模式)
SKILL_PARAMETERS_FILE_PATH = "/home/admin/.openclaw/workspace/skills/skill_parameters.json"
DEFAULT_PARAMETERS_PATH = SKILL_PARAMETERS_FILE_PATH


class SkillParameterService:
    """技能参数管理服务 - 通过 DeviceFileSystem 读写文件"""

    def __init__(self, device_fs, file_path: str | None = None, *, engine_io_enabled: bool = True):
        """
        初始化服务

        Args:
            device_fs: DeviceFileSystem 实例
            file_path: 自定义参数文件路径（默认使用 DEFAULT_PARAMETERS_PATH）
            engine_io_enabled: 是否对 engine 读写 ``skill_parameters.json``。teclaw
                不使用该文件（engine 不持有/不消费它），故由工厂置 ``False``，使
                load/save 成为 no-op，不向 engine 发起任何读写。
        """
        self._device_fs = device_fs
        self._file_path = file_path or SKILL_PARAMETERS_FILE_PATH
        self._engine_io_enabled = engine_io_enabled
        self._data: dict[str, Any] = {}
        # 注意：不在这里调用 _load()，因为需要异步调用
        # 由调用方在需要时调用 async_load()

    async def async_load(self) -> None:
        """异步加载参数文件"""
        if not self._engine_io_enabled:
            self._data = {"parameters": {}}
            logger.info("[SkillParameterService] engine IO disabled (teclaw); skipping load")
            return
        try:
            content = await self._device_fs.read_file(self._file_path)
            if content:
                self._data = json.loads(content.decode("utf-8"))
                logger.info(f"[SkillParameterService] Loaded parameters, size={len(content)}")
            else:
                self._data = {"parameters": {}}
                logger.info("[SkillParameterService] No existing parameters, initialized empty")
        except FileNotFoundError:
            self._data = {"parameters": {}}
            logger.info("[SkillParameterService] File not found, initialized empty")
        except json.JSONDecodeError as e:
            logger.warning(f"[SkillParameterService] Failed to parse parameters JSON: {e}")
            self._data = {"parameters": {}}
        except Exception as e:
            logger.warning(f"[SkillParameterService] Failed to load parameters: {e}")
            self._data = {"parameters": {}}

    async def async_save(self) -> bool:
        """异步保存参数文件

        Returns:
            bool: 保存是否成功
        """
        if not self._engine_io_enabled:
            logger.info("[SkillParameterService] engine IO disabled (teclaw); skipping save")
            return False
        try:
            self._data["updated_at"] = datetime.utcnow().isoformat()
            content = json.dumps(self._data, indent=2, ensure_ascii=False).encode("utf-8")
            await self._device_fs.write_file(self._file_path, content)
            logger.info(f"[SkillParameterService] Saved parameters, size={len(content)}")
            return True
        except Exception as e:
            logger.error(f"[SkillParameterService] Failed to save parameters: {e}")
            return False

    def get_skill_parameters(self, skill_name: str) -> dict[str, Any]:
        """获取指定技能的参数（同步方法，需要先调用 async_load）"""
        return self._data.get("parameters", {}).get(skill_name, {})

    async def save_skill_parameters(self, skill_name: str, parameters: dict[str, Any]) -> bool:
        """保存指定技能的参数

        Args:
            skill_name: 技能名称
            parameters: 参数字典

        Returns:
            bool: 保存是否成功
        """
        if "parameters" not in self._data:
            self._data["parameters"] = {}

        self._data["parameters"][skill_name] = parameters
        return await self.async_save()

    async def delete_skill_parameters(self, skill_name: str) -> bool:
        """删除指定技能的参数

        Returns:
            bool: 保存是否成功
        """
        if "parameters" in self._data:
            self._data["parameters"].pop(skill_name, None)
            return await self.async_save()
        return True

    def get_all_parameters(self) -> dict[str, dict[str, Any]]:
        """获取所有技能参数（同步方法，需要先调用 async_load）"""
        return self._data.get("parameters", {})

    def check_parameters_required(self, skill_name: str, parameter_schema: list) -> tuple:
        """检查技能是否需要配置参数

        Returns: (是否需要配置，未配置的参数列表)
        """
        if not parameter_schema:
            return False, []

        user_parameters = self.get_skill_parameters(skill_name)

        missing_params = []
        for param in parameter_schema:
            if param.get('required') and not user_parameters.get(param['name']):
                missing_params.append(param)

        return len(missing_params) > 0, missing_params
