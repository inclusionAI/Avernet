import hashlib
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agentclaw.community.core.digital_employee.contracts import DigitalEmployeeArtifactStorage, DigitalEmployeeError
from agentclaw.community.core.digital_employee.packages import DigitalEmployeePackageReader
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.kernel.bot_config import StoreRef


@pytest.fixture
def s():
    local, factory, objects = Mock(), Mock(), Mock()
    objects.put_object.return_value = True
    objects.sign_url.return_value = "https://objects.example.test/scan.zip"
    validator = SkillPackageValidator(SkillParser())
    files = [("SKILL.md", b"---\nname: useful-skill\ndescription: Example\n---\nRun script."), ("scripts/tool.py", b"print('real package file')\n")]
    content = validator.pack_directory(files)
    local.export_installed_package = AsyncMock(return_value=content)
    factory.create.return_value.get_repository_skill_package_files.return_value = files
    bot = {"id": 17, "env": "dev", "bot_id": "bot", "owner_id": "owner", "entity_id": "owner", "active_engine": "openclaw"}
    reader = DigitalEmployeePackageReader(local, factory, validator, objects, DigitalEmployeeArtifactStorage("bucket"))
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("locator", ["local://skills-local/useful-skill", "git://tools/useful-skill"])
def test_scan_package_contains_complete_content_and_stable_hash(s, locator):
    asset = SimpleNamespace(skill_id=9, name="useful-skill", git_path=locator)
    package = s.reader.read(s.bot, asset)
    key, content = s.objects.put_object.call_args.args
    assert package["sha256"] == hashlib.sha256(content).hexdigest()
    assert package["key"] == key
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.read("scripts/tool.py") == s.files[1][1]
    s.objects.sign_url.assert_called_once_with(key)


def test_scan_upload_failure_never_returns_a_link(s):
    s.objects.put_object.return_value = False
    with pytest.raises(DigitalEmployeeError, match="保存失败"):
        s.reader.store(s.bot, "9", s.content)
    s.objects.sign_url.assert_not_called()


def test_portable_published_package_reads_only_declared_store_prefix(s):
    s.objects.list_objects.return_value = ["published/42/skill/SKILL.md", "published/42/skill/scripts/tool.py"]
    s.objects.get_object.side_effect = [s.files[0][1], s.files[1][1]]
    package = s.reader.from_store(s.bot, "9", StoreRef(type="oss", bucket="bucket", base="published/42"), "skill")
    assert package["sha256"] == hashlib.sha256(s.content).hexdigest()
    assert s.objects.list_objects.call_args.args == ("published/42/skill/",)


def test_portable_package_rejects_wrong_bucket(s):
    with pytest.raises(DigitalEmployeeError, match="存储"):
        s.reader.from_store(s.bot, "9", StoreRef(type="oss", bucket="other-bucket", base="published/42"), "skill")
    s.objects.get_object.assert_not_called()
