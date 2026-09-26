"""Publish content-addressed scan bundles from the existing Skill content readers."""
import asyncio
import hashlib
from pathlib import Path, PurePosixPath
from typing import Any
from agentclaw.community.kernel.bot_config import StoreRef
from injector import inject

from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeError, DigitalEmployeeArtifactStorage
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import LocalSkillUploadServiceProtocol
from agentclaw.community.core.skill_center.skill_service_factory_protocol import SkillServiceFactoryProtocol
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator, MAX_FILES, MAX_FILE_BYTES, MAX_EXPANDED_BYTES
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


class DigitalEmployeePackageReader:
    @inject
    def __init__(self, local: LocalSkillUploadServiceProtocol, factory: SkillServiceFactoryProtocol,
                 validator: SkillPackageValidator, objects: ObjectStoragePlugin, artifact_storage: DigitalEmployeeArtifactStorage) -> None:
        self._local, self._factory, self._validator, self._objects = local, factory, validator, objects
        self._artifact_storage = artifact_storage

    def read(self, bot: dict[str, Any], asset) -> dict[str, str]:
        if asset.git_path.startswith("local://"):
            content = asyncio.run(self._local.export_installed_package(
                bot=bot, bot_id=bot["bot_id"], owner_id=bot["owner_id"], name=asset.name,
            ))
        elif asset.git_path.startswith("git://"):
            service = self._factory.create(entity_id=bot["entity_id"], bot_owner_id=bot["owner_id"],
                                           bot_id=bot["bot_id"], engine_type=bot["active_engine"])
            files = service.get_repository_skill_package_files(str(asset.skill_id))
            content = self._validator.pack_directory(files)
            self._validator.validate_legacy_local_zip(content)
        else:
            raise DigitalEmployeeError("Skill 没有可读取的完整内容来源")
        return self.store(bot, str(asset.skill_id), content)

    def store(self, bot: dict[str, Any], skill_id: str, content: bytes) -> dict[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        scope = hashlib.sha256(f"{get_current_avernet_tenant()}:{bot['env']}:{bot['id']}:{skill_id}".encode()).hexdigest()
        key = f"digital-employee/packages/{scope}/{digest}.zip"
        if not self._objects.put_object(key, content):
            raise DigitalEmployeeError("Skill 扫描包保存失败")
        return {"sha256": digest, "key": key, "url": self.sign(key)}

    def sign(self, key: str) -> str:
        url = self._objects.sign_url(key)
        if not url:
            raise DigitalEmployeeError("Skill 扫描包签名失败")
        return url

    def from_directory(self, bot: dict[str, Any], skill_id: str, directory: Path) -> dict[str, str]:
        root = directory.resolve(strict=True)
        files = []
        total = 0
        for path in sorted(root.rglob("*")):
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise DigitalEmployeeError("Skill 文件超出扫描包目录")
            if resolved.is_file():
                size = resolved.stat().st_size
                if len(files) >= MAX_FILES or size > MAX_FILE_BYTES or total + size > MAX_EXPANDED_BYTES:
                    raise DigitalEmployeeError("Skill 扫描包文件数量或大小超限")
                with resolved.open("rb") as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                total += len(data)
                if len(data) > MAX_FILE_BYTES or total > MAX_EXPANDED_BYTES:
                    raise DigitalEmployeeError("Skill 扫描包大小超限")
                files.append((path.relative_to(root).as_posix(), data))
        content = self._validator.pack_directory(files)
        self._validator.validate_legacy_local_zip(content)
        return self.store(bot, skill_id, content)

    def from_store(self, bot: dict[str, Any], skill_id: str, store, path: str) -> dict[str, str]:
        if store.type != "oss" or store.bucket != self._artifact_storage.bucket_name or not store.base:
            raise DigitalEmployeeError("发布 Skill 的内容存储与当前配置不匹配")
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise DigitalEmployeeError("发布 Skill 内容路径无效")
        prefix = f"{store.base.rstrip('/')}/{path.rstrip('/')}/"
        keys = self._objects.list_objects(prefix, max_keys=MAX_FILES + 1)
        if not keys or len(keys) > MAX_FILES:
            raise DigitalEmployeeError("发布 Skill 完整文件清单不可用或超限")
        files = []
        total = 0
        for key in sorted(keys):
            if not key.startswith(prefix):
                raise DigitalEmployeeError("发布 Skill 文件超出其存储目录")
            name = key[len(prefix):]
            if not name or name.endswith("/"):
                continue
            data = self._objects.get_object(key)
            if data is None:
                raise DigitalEmployeeError("发布 Skill 文件读取失败")
            total += len(data)
            if len(data) > MAX_FILE_BYTES or total > MAX_EXPANDED_BYTES:
                raise DigitalEmployeeError("发布 Skill 扫描包大小超限")
            files.append((name, data))
        content = self._validator.pack_directory(files)
        self._validator.validate_legacy_local_zip(content)
        return self.store(bot, skill_id, content)

    def from_shared_store(self, bot: dict[str, Any], skill_id: str, prefix: str, path: str) -> dict[str, str]:
        store = StoreRef(type="oss", bucket=self._artifact_storage.bucket_name, base=prefix)
        return self.from_store(bot, skill_id, store, path)
