"""Default CLI capability manifest and Passport-scope behavior."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from agentclaw.community.core.mcp.services.cli_capabilities import (
    CliCapabilityManifestResolver,
    merge_cli_scope,
)
from agentclaw.community.core.mcp.services._defaults import get_default_cli_items
from agentclaw.community.plugin_api.passport import extract_cli_items


MANIFEST = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "agentclaw"
    / "community"
    / "configs"
    / "cli-capabilities.yaml"
)


def test_supported_profiles_resolve_exactly_to_two_default_clis() -> None:
    """Changing a Claude template must not silently grant Default CLIs."""
    resolver = CliCapabilityManifestResolver(MANIFEST)

    assert [item["cli_code"] for item in resolver.required_cli_items("openclaw", None)] == [
        "dataphin",
        "deepinsight-cli",
    ]
    assert [
        item["cli_code"]
        for item in resolver.required_cli_items("claude_code", "generalCC")
    ] == ["dataphin", "deepinsight-cli"]
    assert resolver.required_cli_items("claude_code", "normalCC") == []
    assert resolver.required_cli_items("aicoding", None) == []
    assert resolver.is_supported_profile("openclaw", None) is True
    assert resolver.is_supported_profile("claude_code", "normalCC") is False


def test_generalcc_preserves_legacy_aicoding_clis_at_creation() -> None:
    """YAML registration supplements, rather than replaces, the create-time defaults."""
    assert get_default_cli_items("claude_code", "generalCC") == get_default_cli_items("aicoding")


def test_history_wins_while_sparse_caller_override_wins_identity() -> None:
    """A restart keeps historical metadata but applies the requested identity."""
    history = [
        {
            "cli_code": "dataphin",
            "cli_name": "old name",
            "cli_desc": "old description",
            "identity_mode": "owner",
        },
        {
            "cli_code": "custom",
            "cli_name": "custom cli",
            "identity_mode": "caller",
        },
    ]
    defaults = CliCapabilityManifestResolver(MANIFEST).required_cli_items(
        "openclaw", None
    )

    result = merge_cli_scope(history, defaults, {"dataphin": "caller"})

    assert result == [
        {
            "cli_code": "dataphin",
            "cli_name": "old name",
            "cli_desc": "old description",
            "identity_mode": "caller",
        },
        {
            "cli_code": "custom",
            "cli_name": "custom cli",
            "identity_mode": "caller",
        },
        {
            "cli_code": "deepinsight-cli",
            "cli_name": "deepinsight-cli",
            "cli_desc": "DeepInsight 命令行工具",
            "identity_mode": "owner",
        },
    ]


def test_agentpass_cli_identity_is_preserved_and_invalid_values_fail_closed() -> None:
    """Dropping or coercing an unknown identity would silently widen a grant."""
    assert extract_cli_items({"clis": [{"cli_code": "di", "identity_mode": "caller"}]}) == [
        {"cli_code": "di", "cli_name": None, "cli_desc": None, "identity_mode": "caller"}
    ]

    with pytest.raises(ValueError, match="identity mode must be owner or caller"):
        extract_cli_items({"clis": [{"cli_code": "di", "identity_mode": "delegate"}]})


def _manifest_document() -> dict:
    return {
        "version": 1,
        "manifest_version": "test-v1",
        "catalog": {
            "dataphin": {
                "cli_name": "dataphin-cli",
                "cli_desc": "Dataphin CLI",
                "executable": "dataphin",
                "default_identity_mode": "owner",
                "install": {"installer": "acli", "argv": ["install", "dataphin"]},
                "probe_argv": ["dataphin", "--version"],
            }
        },
        "profiles": [
            {
                "id": "openclaw-default",
                "match": {"engine_type": "openclaw"},
                "default_cli_codes": ["dataphin"],
            }
        ],
    }


def _write_manifest(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "cli-capabilities.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


@pytest.mark.parametrize("raw", ["profiles: [", "- not-a-manifest"])
def test_manifest_rejects_unreadable_or_non_mapping_documents(
    tmp_path: Path, raw: str
) -> None:
    """Deployment policy must fail before an invalid manifest can grant a CLI."""
    path = tmp_path / "cli-capabilities.yaml"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError):
        CliCapabilityManifestResolver(path)


@pytest.mark.parametrize(
    "case",
    [
        "empty_catalog",
        "bad_catalog_code",
        "bad_catalog_entry",
        "missing_metadata",
        "bad_executable",
        "bad_installer",
        "bad_argv",
        "profiles_not_list",
        "profile_not_mapping",
        "duplicate_profile_id",
        "bad_match",
        "bad_template",
        "duplicate_codes",
        "unknown_code",
    ],
)
def test_manifest_rejects_invalid_catalog_and_profile_schema(
    tmp_path: Path, case: str
) -> None:
    """Schema checks prevent untrusted config from becoming an executable policy."""
    document = deepcopy(_manifest_document())
    catalog_entry = document["catalog"]["dataphin"]
    profile = document["profiles"][0]
    if case == "empty_catalog":
        document["catalog"] = {}
    elif case == "bad_catalog_code":
        document["catalog"] = {"not valid": catalog_entry}
    elif case == "bad_catalog_entry":
        document["catalog"]["dataphin"] = []
    elif case == "missing_metadata":
        catalog_entry["cli_name"] = None
    elif case == "bad_executable":
        catalog_entry["executable"] = "bad executable"
    elif case == "bad_installer":
        catalog_entry["install"] = {"installer": None, "argv": ["install"]}
    elif case == "bad_argv":
        catalog_entry["probe_argv"] = ["bad argument"]
    elif case == "profiles_not_list":
        document["profiles"] = {}
    elif case == "profile_not_mapping":
        document["profiles"] = ["not-a-profile"]
    elif case == "duplicate_profile_id":
        document["profiles"] = [profile, deepcopy(profile)]
    elif case == "bad_match":
        profile["match"] = {}
    elif case == "bad_template":
        profile["match"] = {"engine_type": "openclaw", "template_type": 3}
    elif case == "duplicate_codes":
        profile["default_cli_codes"] = ["dataphin", "dataphin"]
    elif case == "unknown_code":
        profile["default_cli_codes"] = ["missing"]

    with pytest.raises(ValueError):
        CliCapabilityManifestResolver(_write_manifest(tmp_path, document))


def test_merge_cli_scope_rejects_malformed_history_and_defaults() -> None:
    """The scope writer must fail closed before a malformed item reaches Passport."""
    with pytest.raises(ValueError, match="scope item"):
        merge_cli_scope(["not-a-cli"], [])
    with pytest.raises(ValueError, match="code"):
        merge_cli_scope([], [{"cli_code": "not valid", "identity_mode": "owner"}])

    result = merge_cli_scope([{"cli_code": "legacy-cli"}], [])

    assert result == [{"cli_code": "legacy-cli", "identity_mode": "owner"}]
