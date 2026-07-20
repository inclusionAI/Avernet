"""Unit tests for the rebind-architect-bot feature.

Covers the new methods:
- ArchitectRebindService._get_architect_domain_or_raise
- ArchitectRebindService._rebind_coding_bot_to_architect
- ArchitectRebindService.rebind_architect_bot (single)
- ArchitectRebindService.rebind_architect_bot_batch

Contract pinned here (open source): the owner boundary lives on the architect
side (owner-scoped), and the per-coding-bot helper only validates existence +
type. The batch flow returns a per-item error_code map and never leaks the
template/token ciphertext.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotPermissionError,
    BotServiceError,
)

from agentclaw.community.core.aicoding.services.architect_rebind_service import (
    ArchitectRebindService,
)


ARCH_ID = "arch1"
OPERATOR = "u001"


def _make_service() -> ArchitectRebindService:
    svc = ArchitectRebindService.__new__(ArchitectRebindService)
    svc._repository = MagicMock()
    svc._template_service = MagicMock()
    return svc


def _architect_bot(bot_id: str = ARCH_ID, is_domain: bool = True) -> dict:
    return {
        "bot_id": bot_id,
        "owner_id": OPERATOR,
        "ext": {"is_domain_bot": True} if is_domain else {},
    }


def _coding_bot(bot_id: str = "c1", template_type: str = "applicationCoding") -> dict:
    return {"bot_id": bot_id, "template_type": template_type, "owner_id": OPERATOR}


def _template(bot_id: str = "c1", ext: dict | None = None) -> dict:
    return {"bot_id": bot_id, "ext": ext if ext is not None else {}}


# ===========================================================================
# _get_architect_domain_or_raise
# ===========================================================================
class TestGetArchitectDomainOrRaise:
    def test_not_owned_raises_permission_and_is_owner_scoped(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None
        with pytest.raises(BotPermissionError):
            svc._get_architect_domain_or_raise(ARCH_ID, OPERATOR)
        # owner-scoped, never the plain get_by_id (avoid enumeration leak)
        svc._repository.get_by_id_and_owner.assert_called_once_with(ARCH_ID, OPERATOR)
        svc._repository.get_by_id.assert_not_called()

    def test_not_a_domain_bot_raises_service_error(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _architect_bot(is_domain=False)
        with pytest.raises(BotServiceError):
            svc._get_architect_domain_or_raise(ARCH_ID, OPERATOR)

    def test_ext_none_raises_service_error(self):
        svc = _make_service()
        arch = _architect_bot()
        arch["ext"] = None
        svc._repository.get_by_id_and_owner.return_value = arch
        with pytest.raises(BotServiceError):
            svc._get_architect_domain_or_raise(ARCH_ID, OPERATOR)

    def test_is_domain_bot_not_boolean_raises_service_error(self):
        svc = _make_service()
        arch = _architect_bot()
        arch["ext"] = {"is_domain_bot": "true"}  # truthy string, but not boolean True
        svc._repository.get_by_id_and_owner.return_value = arch
        with pytest.raises(BotServiceError):
            svc._get_architect_domain_or_raise(ARCH_ID, OPERATOR)

    def test_success_returns_architect(self):
        svc = _make_service()
        arch = _architect_bot()
        svc._repository.get_by_id_and_owner.return_value = arch
        assert svc._get_architect_domain_or_raise(ARCH_ID, OPERATOR) is arch

    def test_ext_stored_as_json_string_is_deserialized_and_succeeds(self):
        svc = _make_service()
        arch = _architect_bot()
        arch["ext"] = '{"is_domain_bot": true}'
        svc._repository.get_by_id_and_owner.return_value = arch
        # ext stored as JSON string -> must be deserialized, then pass domain check
        assert svc._get_architect_domain_or_raise(ARCH_ID, OPERATOR) is arch

    def test_ext_invalid_json_string_is_coerced_and_raises(self):
        svc = _make_service()
        arch = _architect_bot()
        arch["ext"] = "not-json"
        svc._repository.get_by_id_and_owner.return_value = arch
        # broken JSON string -> coerced to {} -> not a domain bot -> service error
        with pytest.raises(BotServiceError):
            svc._get_architect_domain_or_raise(ARCH_ID, OPERATOR)


# ===========================================================================
# _rebind_coding_bot_to_architect
# ===========================================================================
class TestRebindCodingBotToArchitect:
    def test_coding_bot_not_found_raises_not_found(self):
        svc = _make_service()
        svc._repository.get_by_id.return_value = None
        with pytest.raises(BotNotFoundError):
            svc._rebind_coding_bot_to_architect("missing", ARCH_ID, OPERATOR)
        # owner check moved to architect side -> not used here
        svc._repository.get_by_id_and_owner.assert_not_called()

    def test_wrong_template_type_raises_service_error(self):
        svc = _make_service()
        svc._repository.get_by_id.return_value = _coding_bot(template_type="other")
        with pytest.raises(BotServiceError):
            svc._rebind_coding_bot_to_architect("c1", ARCH_ID, OPERATOR)
        svc._template_service.get_template.assert_not_called()

    def test_template_missing_raises_service_error(self):
        svc = _make_service()
        svc._repository.get_by_id.return_value = _coding_bot()
        svc._template_service.get_template.return_value = None
        with pytest.raises(BotServiceError):
            svc._rebind_coding_bot_to_architect("c1", ARCH_ID, OPERATOR)

    def test_ext_not_dict_is_coerced_and_rebinds(self):
        svc = _make_service()
        svc._repository.get_by_id.return_value = _coding_bot()
        svc._template_service.get_template.return_value = {"bot_id": "c1", "ext": None}
        svc._template_service.update_template.return_value = {
            "bot_id": "c1", "ext": {"architect_bot_id": ARCH_ID}
        }
        result = svc._rebind_coding_bot_to_architect("c1", ARCH_ID, OPERATOR)
        assert result["changed"] is True
        # ext None -> coerced to {} -> only architect_bot_id set
        new_ext = svc._template_service.update_template.call_args.args[1]
        assert new_ext == {"architect_bot_id": ARCH_ID}

    def test_idempotent_noop_does_not_persist(self):
        svc = _make_service()
        svc._repository.get_by_id.return_value = _coding_bot()
        svc._template_service.get_template.return_value = _template(
            ext={"architect_bot_id": ARCH_ID}
        )
        result = svc._rebind_coding_bot_to_architect("c1", ARCH_ID, OPERATOR)
        assert result["changed"] is False
        assert result["previous_architect_bot_id"] == ARCH_ID
        svc._template_service.update_template.assert_not_called()

    def test_success_updates_ext_and_returns_changed_true(self):
        svc = _make_service()
        svc._repository.get_by_id.return_value = _coding_bot()
        svc._template_service.get_template.return_value = _template(
            ext={"architect_bot_id": "old", "token": "enc:v1:abc"}
        )
        updated = {"bot_id": "c1", "ext": {"architect_bot_id": ARCH_ID, "token": "enc:v1:abc"}}
        svc._template_service.update_template.return_value = updated
        result = svc._rebind_coding_bot_to_architect("c1", ARCH_ID, OPERATOR)
        assert result["changed"] is True
        assert result["previous_architect_bot_id"] == "old"
        assert result["template"] is updated
        call = svc._template_service.update_template.call_args
        assert call.args[0] == "c1"
        assert call.args[1] == {"architect_bot_id": ARCH_ID, "token": "enc:v1:abc"}
        assert call.kwargs == {"template_type": "applicationCoding"}


# ===========================================================================
# rebind_architect_bot (single)
# ===========================================================================
class TestRebindArchitectBot:
    def test_empty_args_raises_service_error(self):
        svc = _make_service()
        with pytest.raises(BotServiceError):
            svc.rebind_architect_bot("", ARCH_ID, OPERATOR)
        with pytest.raises(BotServiceError):
            svc.rebind_architect_bot("c1", "", OPERATOR)

    def test_coding_equals_architect_raises(self):
        svc = _make_service()
        with pytest.raises(BotServiceError):
            svc.rebind_architect_bot(ARCH_ID, ARCH_ID, OPERATOR)

    def test_architect_not_owned_raises_permission(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None
        with pytest.raises(BotPermissionError):
            svc.rebind_architect_bot("c1", ARCH_ID, OPERATOR)
        # must not reach the coding-bot lookup
        svc._repository.get_by_id.assert_not_called()

    def test_success_returns_change(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _architect_bot()
        svc._repository.get_by_id.return_value = _coding_bot()
        svc._template_service.get_template.return_value = _template(ext={"architect_bot_id": "old"})
        svc._template_service.update_template.return_value = {
            "bot_id": "c1", "ext": {"architect_bot_id": ARCH_ID}
        }
        result = svc.rebind_architect_bot("c1", ARCH_ID, OPERATOR)
        assert result["changed"] is True
        # architect owner check: owner-scoped exactly once
        assert svc._repository.get_by_id_and_owner.call_count == 1
        # coding-bot existence: plain get_by_id (NOT owner-scoped) exactly once
        svc._repository.get_by_id.assert_called_once_with("c1")


# ===========================================================================
# rebind_architect_bot_batch
# ===========================================================================
class TestRebindArchitectBotBatch:
    def test_empty_architect_raises(self):
        svc = _make_service()
        with pytest.raises(BotServiceError):
            svc.rebind_architect_bot_batch(["c1"], "", OPERATOR)

    def test_empty_coding_ids_raises(self):
        svc = _make_service()
        with pytest.raises(BotServiceError):
            svc.rebind_architect_bot_batch([], ARCH_ID, OPERATOR)

    def test_all_blank_coding_ids_raises(self):
        svc = _make_service()
        with pytest.raises(BotServiceError):
            svc.rebind_architect_bot_batch(["", ""], ARCH_ID, OPERATOR)

    def test_architect_in_coding_ids_raises_before_lookup(self):
        svc = _make_service()
        with pytest.raises(BotServiceError):
            svc.rebind_architect_bot_batch(["c1", ARCH_ID], ARCH_ID, OPERATOR)
        svc._repository.get_by_id_and_owner.assert_not_called()

    def test_architect_check_called_once_for_n_items(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _architect_bot()
        svc._repository.get_by_id.return_value = _coding_bot()
        svc._template_service.get_template.return_value = _template(ext={"architect_bot_id": "old"})
        svc._template_service.update_template.return_value = {"bot_id": "x"}
        svc.rebind_architect_bot_batch(["c1", "c2", "c3"], ARCH_ID, OPERATOR)
        assert svc._repository.get_by_id_and_owner.call_count == 1

    def test_dedup_preserves_order(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _architect_bot()
        svc._rebind_coding_bot_to_architect = MagicMock(
            return_value={"changed": True, "previous_architect_bot_id": "old"}
        )
        result = svc.rebind_architect_bot_batch(["b", "a", "b", "c", "a"], ARCH_ID, OPERATOR)
        called = [c.args[0] for c in svc._rebind_coding_bot_to_architect.call_args_list]
        assert called == ["b", "a", "c"]
        assert result["total"] == 3
        assert result["succeeded"] == 3

    def test_architect_not_owned_whole_batch_rejected(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = None
        with pytest.raises(BotPermissionError):
            svc.rebind_architect_bot_batch(["c1"], ARCH_ID, OPERATOR)

    def test_mixed_results_per_item(self):
        svc = _make_service()
        svc._repository.get_by_id_and_owner.return_value = _architect_bot()
        coding = {
            "c1": _coding_bot("c1"),                         # success -> changed True
            "c2": None,                                       # not_found
            "c3": _coding_bot("c3", template_type="other"),   # invalid (non-appcoding)
            "c4": _coding_bot("c4"),                          # forbidden (update raises BotPermissionError)
            "c5": _coding_bot("c5"),                          # error   (update raises ValueError)
        }
        svc._repository.get_by_id.side_effect = lambda bid: coding[bid]
        templates = {
            "c1": _template("c1", ext={"architect_bot_id": "old"}),
            "c4": _template("c4", ext={"architect_bot_id": "old"}),
            "c5": _template("c5", ext={"architect_bot_id": "old"}),
        }
        svc._template_service.get_template.side_effect = lambda bid: templates[bid]
        updated = {"bot_id": "x", "ext": {"architect_bot_id": ARCH_ID}}

        def _update(bid, ext, template_type=None):
            if bid == "c1":
                return updated
            if bid == "c4":
                raise BotPermissionError("forbidden-c4")
            if bid == "c5":
                raise ValueError("boom-c5")
            raise AssertionError(f"unexpected update call for {bid}")

        svc._template_service.update_template.side_effect = _update

        result = svc.rebind_architect_bot_batch(
            ["c1", "c2", "c3", "c4", "c5"], ARCH_ID, OPERATOR
        )

        assert result["total"] == 5
        assert result["succeeded"] == 1
        assert result["failed"] == 4
        by_id = {r["bot_id"]: r for r in result["results"]}
        assert by_id["c1"] == {
            "bot_id": "c1",
            "success": True,
            "changed": True,
            "previous_architect_bot_id": "old",
            "architect_bot_id": ARCH_ID,
        }
        assert by_id["c2"]["success"] is False and by_id["c2"]["error_code"] == "not_found"
        assert by_id["c3"]["success"] is False and by_id["c3"]["error_code"] == "invalid"
        assert by_id["c4"]["success"] is False and by_id["c4"]["error_code"] == "forbidden"
        assert by_id["c5"]["success"] is False and by_id["c5"]["error_code"] == "error"
        # success item must never leak template / token ciphertext
        assert "template" not in by_id["c1"]
        assert "token" not in by_id["c1"]
        # architect owner-scoped check happened exactly once for the whole batch
        assert svc._repository.get_by_id_and_owner.call_count == 1
