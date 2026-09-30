"""``entity_id`` is the addressed-owner parameter; ``owner_id`` its retiring alias.

The rule lives in one function, ``principal.addressed_owner``, because two
readers depend on it agreeing with itself: the raw query read the grant check
makes before any handler runs, and the declared parameters the engine-runtime
handlers consume. These tests pin the rule; the end-to-end files
(``engine_runtime/test_app_only_caller.py``, ``test_skills_shared_bot_grant.py``,
``work_orders/test_work_order_contract.py``) pin that every reader applies it.
"""

from __future__ import annotations

import pytest
from fastapi.exceptions import RequestValidationError

from agentclaw.community.adapters.http.openapi_v1.principal import (
    ENTITY_ID_QUERY,
    OWNER_ID_QUERY,
    addressed_owner,
)


def test_entity_id_is_taken_when_present() -> None:
    assert addressed_owner("u-new", None) == "u-new"


def test_owner_id_still_answers_while_entity_id_is_absent() -> None:
    """A client built against the earlier contract keeps working unchanged."""
    assert addressed_owner(None, "u-old") == "u-old"


def test_naming_neither_means_unnamed() -> None:
    assert addressed_owner(None, None) is None


def test_both_spellings_may_agree() -> None:
    """A client mid-migration may send both; agreement is not a conflict."""
    assert addressed_owner("u-1", "u-1") == "u-1"


def test_both_spellings_disagreeing_is_refused_as_invalid() -> None:
    """Not a preference to resolve: the request does not say which bot it means.

    Refused as a validation failure, the same 422 a malformed value gets, and
    the message names the parameters but neither value — both are unbounded
    caller input and the handler logs the message verbatim.
    """
    with pytest.raises(RequestValidationError) as refused:
        addressed_owner("u-1", "u-2")

    [error] = refused.value.errors()
    assert error["loc"] == ("query", ENTITY_ID_QUERY)
    assert ENTITY_ID_QUERY in error["msg"] and OWNER_ID_QUERY in error["msg"]
    assert "u-1" not in error["msg"] and "u-2" not in error["msg"]
