from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from singlebox_coverage_manifest_check import validate_manifest  # noqa: E402


def _backend(tmp_path: Path) -> Path:
    plugin_api = tmp_path / "src/agentclaw/community/plugin_api/passport.py"
    plugin_api.parent.mkdir(parents=True)
    plugin_api.write_text(
        "class PassportPlugin(Plugin, Protocol):\n"
        "    def freeze_agent_passport(self): ...\n",
        encoding="utf-8",
    )
    implementation = tmp_path / "src/agentclaw/community/plugins/local/passport.py"
    implementation.parent.mkdir(parents=True)
    implementation.write_text(
        "class LocalPassportPlugin(PassportPlugin):\n"
        "    def freeze_agent_passport(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    return tmp_path


def test_validate_manifest_accepts_real_plugin_evidence(tmp_path: Path):
    manifest = {
        "modules": {
            "dormant": {
                "plugin_api": {
                    "items": [
                        {
                            "key": "PassportPlugin.freeze_agent_passport",
                            "evidence": {
                                "path": "src/agentclaw/community/plugins/local/passport.py",
                                "symbol": "LocalPassportPlugin.freeze_agent_passport",
                            },
                        }
                    ]
                }
            }
        }
    }

    assert validate_manifest(manifest, backend_root=_backend(tmp_path)) == []


def test_validate_manifest_rejects_core_seam_as_plugin_api(tmp_path: Path):
    manifest = {
        "modules": {
            "resources": {
                "plugin_api": {
                    "items": [
                        {
                            "key": "ResourceRepository.create",
                            "evidence": {
                                "path": "src/agentclaw/community/core/resources/repository.py",
                                "symbol": "ResourceRepository.create",
                            },
                        }
                    ]
                }
            }
        }
    }

    errors = validate_manifest(manifest, backend_root=_backend(tmp_path))

    assert errors == [
        "resources: ResourceRepository is not a declared Plugin Protocol"
    ]


def test_validate_manifest_requires_offline_evidence_for_plugin_items(tmp_path: Path):
    manifest = {
        "modules": {
            "dormant": {
                "plugin_api": {
                    "items": ["PassportPlugin.freeze_agent_passport"]
                }
            }
        }
    }

    errors = validate_manifest(manifest, backend_root=_backend(tmp_path))

    assert errors == [
        "dormant: PassportPlugin.freeze_agent_passport must declare offline evidence"
    ]


def test_validate_manifest_rejects_evidence_from_wrong_implementation(tmp_path: Path):
    backend_root = _backend(tmp_path)
    source = backend_root / "src/agentclaw/community/plugins/local/passport.py"
    source.write_text(
        "class LocalPassportPlugin(UnrelatedPlugin):\n"
        "    def freeze_agent_passport(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    manifest = {
        "modules": {
            "dormant": {
                "plugin_api": {
                    "items": [
                        {
                            "key": "PassportPlugin.freeze_agent_passport",
                            "evidence": {
                                "path": "src/agentclaw/community/plugins/local/passport.py",
                                "symbol": "LocalPassportPlugin.freeze_agent_passport",
                            },
                        }
                    ]
                }
            }
        }
    }

    assert validate_manifest(manifest, backend_root=backend_root) == [
        "dormant: LocalPassportPlugin does not implement PassportPlugin"
    ]


def test_validate_manifest_rejects_evidence_outside_plugins(tmp_path: Path):
    backend_root = _backend(tmp_path)
    source = backend_root / "src/agentclaw/community/core/passport.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class CorePassport(PassportPlugin):\n"
        "    def freeze_agent_passport(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    manifest = {
        "modules": {
            "dormant": {
                "plugin_api": {
                    "items": [
                        {
                            "key": "PassportPlugin.freeze_agent_passport",
                            "evidence": {
                                "path": "src/agentclaw/community/core/passport.py",
                                "symbol": "CorePassport.freeze_agent_passport",
                            },
                        }
                    ]
                }
            }
        }
    }

    assert validate_manifest(manifest, backend_root=backend_root) == [
        "dormant: evidence source must be under "
        "src/agentclaw/community/plugins: "
        "src/agentclaw/community/core/passport.py"
    ]


def test_validate_manifest_rejects_plugin_path_traversal(tmp_path: Path):
    backend_root = _backend(tmp_path)
    source = backend_root / "src/agentclaw/community/core/passport.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class CorePassport(PassportPlugin):\n"
        "    def freeze_agent_passport(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    traversal_path = (
        "src/agentclaw/community/plugins/../../core/passport.py"
    )
    manifest = {
        "modules": {
            "dormant": {
                "plugin_api": {
                    "items": [
                        {
                            "key": "PassportPlugin.freeze_agent_passport",
                            "evidence": {
                                "path": traversal_path,
                                "symbol": "CorePassport.freeze_agent_passport",
                            },
                        }
                    ]
                }
            }
        }
    }

    assert validate_manifest(manifest, backend_root=backend_root) == [
        "dormant: evidence source must be under "
        f"src/agentclaw/community/plugins: {traversal_path}"
    ]
