"""Unit tests for yuque/code-repo comparison helpers in bot_management/utils.py."""

import os
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.utils import (
    _extract_yuque_pairs,
    _extract_code_repo_urls,
    memory_sources_changed,
    trigger_memory_initialization,
    yuque_kb_repos_changed,
)


class TestExtractYuquePairs:
    def test_empty_dict(self):
        assert _extract_yuque_pairs({}) == []

    def test_no_yuque_key(self):
        assert _extract_yuque_pairs({"backend_repo": []}) == []

    def test_yuque_kb_repos_none(self):
        assert _extract_yuque_pairs({"yuque_kb_repos": None}) == []

    def test_yuque_kb_repos_not_list(self):
        assert _extract_yuque_pairs({"yuque_kb_repos": "bad"}) == []

    def test_normal_extraction(self):
        config = {
            "yuque_kb_repos": [
                {"url": "https://yuque.antfin.com/a/b"},
                {"url": "https://yuque.antfin.com/c/d", "token": "tk"},
            ]
        }
        assert _extract_yuque_pairs(config) == [
            ("https://yuque.antfin.com/a/b", ""),
            ("https://yuque.antfin.com/c/d", "tk"),
        ]

    def test_skips_empty_urls(self):
        config = {
            "yuque_kb_repos": [
                {"url": "https://yuque.antfin.com/a/b"},
                {"url": ""},
                {"url": None},
                {"name": "no-url"},
            ]
        }
        assert _extract_yuque_pairs(config) == [
            ("https://yuque.antfin.com/a/b", ""),
        ]

    def test_non_dict_input(self):
        assert _extract_yuque_pairs("not a dict") == []
        assert _extract_yuque_pairs(None) == []

    def test_items_not_dicts_are_skipped(self):
        config = {"yuque_kb_repos": ["string_item", 123, None]}
        assert _extract_yuque_pairs(config) == []


class TestExtractCodeRepoUrls:
    def test_empty_dict(self):
        assert _extract_code_repo_urls({}) == []

    def test_single_backend_repo(self):
        config = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/a/b"}]}
        assert _extract_code_repo_urls(config) == ["https://code.teamclaw.com/a/b"]

    def test_multiple_repo_types(self):
        config = {
            "backend_repo": [{"repo_url": "https://code.teamclaw.com/back"}],
            "frontend_repo": [{"repo_url": "https://code.teamclaw.com/front"}],
            "lib_repo": [{"repo_url": "https://code.teamclaw.com/lib"}],
        }
        assert _extract_code_repo_urls(config) == [
            "https://code.teamclaw.com/back",
            "https://code.teamclaw.com/front",
            "https://code.teamclaw.com/lib",
        ]

    def test_skips_empty_urls(self):
        config = {
            "backend_repo": [
                {"repo_url": "https://code.teamclaw.com/a"},
                {"repo_url": ""},
                {"repo_url": None},
                {"name": "no-url"},
            ]
        }
        assert _extract_code_repo_urls(config) == ["https://code.teamclaw.com/a"]

    def test_non_dict_input(self):
        assert _extract_code_repo_urls("not a dict") == []
        assert _extract_code_repo_urls(None) == []

    def test_repo_key_not_list(self):
        config = {"backend_repo": "bad"}
        assert _extract_code_repo_urls(config) == []

    def test_items_not_dicts_are_skipped(self):
        config = {"backend_repo": ["string", 123]}
        assert _extract_code_repo_urls(config) == []

    def test_none_value_for_repo_key(self):
        config = {"backend_repo": None}
        assert _extract_code_repo_urls(config) == []


class TestMemorySourcesChanged:
    def test_both_empty(self):
        assert memory_sources_changed({}, {}) is False

    def test_yuque_changed(self):
        old = {"yuque_kb_repos": [{"url": "https://a"}]}
        new = {"yuque_kb_repos": [{"url": "https://b"}]}
        assert memory_sources_changed(old, new) is True

    def test_code_repo_changed(self):
        old = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/a"}]}
        new = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/b"}]}
        assert memory_sources_changed(old, new) is True

    def test_both_unchanged(self):
        old = {
            "yuque_kb_repos": [{"url": "https://yuque/a"}],
            "backend_repo": [{"repo_url": "https://code/a"}],
        }
        new = {
            "yuque_kb_repos": [{"url": "https://yuque/a"}],
            "backend_repo": [{"repo_url": "https://code/a"}],
        }
        assert memory_sources_changed(old, new) is False

    def test_only_code_repo_added(self):
        old = {}
        new = {"backend_repo": [{"repo_url": "https://code.teamclaw.com/new"}]}
        assert memory_sources_changed(old, new) is True

    def test_only_code_repo_removed(self):
        old = {"frontend_repo": [{"repo_url": "https://code.teamclaw.com/old"}]}
        new = {}
        assert memory_sources_changed(old, new) is True

    def test_yuque_same_code_repo_changed(self):
        old = {
            "yuque_kb_repos": [{"url": "https://yuque/a"}],
            "backend_repo": [{"repo_url": "https://code/old"}],
        }
        new = {
            "yuque_kb_repos": [{"url": "https://yuque/a"}],
            "backend_repo": [{"repo_url": "https://code/new"}],
        }
        assert memory_sources_changed(old, new) is True

    def test_order_insensitive_code_repos(self):
        old = {
            "backend_repo": [
                {"repo_url": "https://code/a"},
                {"repo_url": "https://code/b"},
            ]
        }
        new = {
            "backend_repo": [
                {"repo_url": "https://code/b"},
                {"repo_url": "https://code/a"},
            ]
        }
        assert memory_sources_changed(old, new) is False

    def test_order_insensitive_yuque(self):
        old = {"yuque_kb_repos": [{"url": "https://a"}, {"url": "https://b"}]}
        new = {"yuque_kb_repos": [{"url": "https://b"}, {"url": "https://a"}]}
        assert memory_sources_changed(old, new) is False

    def test_duplicates_ignored(self):
        old = {"backend_repo": [{"repo_url": "https://a"}, {"repo_url": "https://a"}]}
        new = {"backend_repo": [{"repo_url": "https://a"}]}
        assert memory_sources_changed(old, new) is False

    def test_yuque_team_token_changed(self):
        old = {"yuque_kb_repos": [{"url": "https://a", "token": "old"}]}
        new = {"yuque_kb_repos": [{"url": "https://a", "token": "new"}]}
        assert memory_sources_changed(old, new) is True

    def test_alias_still_works(self):
        assert yuque_kb_repos_changed is memory_sources_changed


class TestTriggerMemoryInitYuquePayload:
    """覆盖 trigger_memory_initialization 中 yuqueUrls 的对象化构造与过滤逻辑。"""

    def _run(self, template_config):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["payload"] = json
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch(
            "agentclaw.community.core.bot_management.utils.get_current_env",
            return_value="pre",
        ), patch(
            "agentclaw.community.core.bot_management.utils.requests.post",
            side_effect=fake_post,
        ):
            # memoryOS endpoint is deployment config (WorkspaceHostingConfig,
            # passed in). env=="pre" ⇒ the _pre value is used.
            trigger_memory_initialization(
                bot_id="b1",
                bot_name="n",
                user_id="u",
                template_config=template_config,
                cookie="c=1",
                aixcore_base_url_pre="https://aixcore.example.com",
            )
        return captured.get("payload", {})

    def test_yuque_urls_built_as_objects_with_team_token(self):
        payload = self._run(
            {
                "yuque_kb_repos": [
                    {"url": "https://yuque.antfin-inc.com/aixcoding/tech"},
                    {"url": "https://yuque.antfin.com/aixcoding/ua4eom", "token": "TK"},
                ]
            }
        )
        assert payload["yuqueUrls"] == [
            {"url": "https://yuque.antfin-inc.com/aixcoding/tech", "teamToken": ""},
            {"url": "https://yuque.antfin.com/aixcoding/ua4eom", "teamToken": "TK"},
        ]

    def test_yuque_skips_empty_and_non_dict(self):
        payload = self._run(
            {
                "yuque_kb_repos": [
                    "string_item",
                    None,
                    {"url": ""},
                    {"url": None},
                    {"url": "https://a", "token": ""},
                ]
            }
        )
        assert payload["yuqueUrls"] == [{"url": "https://a", "teamToken": ""}]

    def test_yuque_urls_absent_when_no_valid_items(self):
        payload = self._run({"yuque_kb_repos": [{"url": ""}, "x"]})
        assert "yuqueUrls" not in payload

    def test_memory_init_skips_when_aixcore_unset(self):
        """No aixcore endpoint configured (community / feature-off) → no HTTP call."""
        called = {"post": False}

        def fake_post(*args, **kwargs):
            called["post"] = True
            return MagicMock(status_code=200)

        with patch(
            "agentclaw.community.core.bot_management.utils.get_current_env", return_value="pre"
        ), patch(
            "agentclaw.community.core.bot_management.utils.requests.post", side_effect=fake_post
        ):
            trigger_memory_initialization(
                bot_id="b1",
                bot_name="n",
                user_id="u",
                template_config={"yuque_kb_repos": [{"url": "https://a"}]},
                cookie="c=1",
                # aixcore_base_url / _pre left at default "" → skip.
            )

        assert called["post"] is False


class TestTemplateFactoryKnowledgeAliases:
    def test_yuque_pairs_include_template_factory_wiki_aliases(self):
        config = {
            "template_key": "normalCC",
            "wiki_knowledge_spaces": [
                {"url": "https://yuque/wiki", "teamToken": "TK1"},
            ],
            "business_wiki_spaces": [
                {"wiki_url": "https://yuque/business", "team_token": "TK2"},
            ],
            "repo_wiki_spaces": [
                {"repo_wiki_url": "https://yuque/repo"},
            ],
        }

        assert _extract_yuque_pairs(config) == [
            ("https://yuque/wiki", "TK1"),
            ("https://yuque/business", "TK2"),
            ("https://yuque/repo", ""),
        ]

    def test_code_repo_urls_include_template_factory_aliases(self):
        config = {
            "template_key": "normalCC",
            "repos": ["https://code/repos-string"],
            "init_repos": [{"url": "https://code/init"}],
            "application_repo_urls": [{"git_url": "git@example.com:a/b.git"}],
        }

        assert _extract_code_repo_urls(config) == [
            "https://code/repos-string",
            "https://code/init",
            "git@example.com:a/b.git",
        ]

    def test_memory_sources_changed_detects_template_factory_aliases(self):
        old = {"template_key": "normalCC", "business_wiki_spaces": [{"url": "https://yuque/old"}]}
        new = {"template_key": "normalCC", "business_wiki_spaces": [{"url": "https://yuque/new"}]}

        assert memory_sources_changed(old, new) is True

    def test_trigger_memory_initialization_payload_uses_aliases(self):
        payload = self._run_with_aliases(
            {
                "template_key": "normalCC",
                "business_wiki_spaces": [
                    {"wiki_url": "https://yuque/business", "teamToken": "TK"},
                ],
                "init_repos": [{"repo_url": "https://code/init"}],
            }
        )

        assert payload["yuqueUrls"] == [
            {"url": "https://yuque/business", "teamToken": "TK"},
        ]
        assert payload["codeRepoUrls"] == ["https://code/init"]

    def _run_with_aliases(self, template_config):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["payload"] = json
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch(
            "agentclaw.community.core.bot_management.utils.get_current_env",
            return_value="pre",
        ), patch(
            "agentclaw.community.core.bot_management.utils.requests.post",
            side_effect=fake_post,
        ):
            trigger_memory_initialization(
                bot_id="b1",
                bot_name="n",
                user_id="u",
                template_config=template_config,
                cookie="c=1",
                aixcore_base_url_pre="https://aixcore.example.com",
            )
        return captured.get("payload", {})


def test_iter_template_list_items_ignores_non_dict_and_extends_lists():
    from agentclaw.community.core.bot_management.utils import _iter_template_list_items

    assert _iter_template_list_items(None, ("repos",)) == []  # type: ignore[arg-type]
    assert _iter_template_list_items({"repos": ["a"], "init_repos": "bad"}, ("repos", "init_repos")) == ["a"]


def test_template_factory_yuque_string_alias_skips_invalid_and_keeps_valid_urls():
    config = {
        "template_uid": "aicoding",
        "business_wiki_spaces": [
            "not-a-url",
            " https://yuque.antfin.com/securitytec/wiki ",
            {"space_url": "https://yuque.antfin.com/securitytec/space", "team_token": "TK"},
        ],
    }

    assert _extract_yuque_pairs(config) == [
        ("https://yuque.antfin.com/securitytec/wiki", ""),
        ("https://yuque.antfin.com/securitytec/space", "TK"),
    ]
