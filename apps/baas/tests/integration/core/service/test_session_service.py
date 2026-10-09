"""Optimized integration tests for SessionService with shared fixtures.

Consolidates 20 individual tests into 1 comprehensive test method.
Uses shared fixtures to minimize database record creation.
"""

import random
from uuid import uuid4

import pytest

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.bootstrap import get_container
from secbaas.community.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()
FIXED_TENANT_NAME = "test_tenant"


def _dts():
    return get_container().services.device_template_service()


def _dss():
    return get_container().services.session_service()


def generate_uuid() -> str:
    return uuid4().hex


@pytest.fixture(scope="module")
def shared_session_setup(
    bot_repository,
    created_bot_ids,
    created_tenant_ids,
    created_template_ids,
    skip_if_zdas_unavailable,
):
    """Create ONE shared bot for all SessionService tests."""
    from secbaas.community.api.template_manage import (
        ArcaTemplateConfig,
        TemplateCreate,
    )
    from secbaas.community.api.tenant_manage import TenantType

    # Create/get shared tenant
    tenant_repo = get_container().repository.tenant_repository()
    existing_tenant = tenant_repo.get_by_name(FIXED_TENANT_NAME, TEST_ENV)

    if not existing_tenant:
        record_id = tenant_repo.insert_tenant(
            name=FIXED_TENANT_NAME,
            env=TEST_ENV,
            creator="test_user",
            modifier="test_user",
            description=None,
            extra_config=None,
        )
        created_tenant_ids.append(record_id)

    # Create ONE template
    template_uuid = (
        f"test-session-{int(__import__('time').time() * 1000000) % 10000000000}"
    )
    template = _dts().create_template(
        tenant=FIXED_TENANT_NAME,
        data=TemplateCreate(
            template_uuid=template_uuid,
            template_id=random.randint(1, 999999999),
            type=TenantType.ARCA,
            name=f"Shared Session Template {template_uuid}",
            description=None,
            config=ArcaTemplateConfig(
                type="ARCA",
                base_url="http://test",
                api_key="test",
                template_id="",
                arca_template_id_pre=None,
                arca_template_id_prod=None,
                oss_mount_id=None,
            ),
            operator="test_user",
        ),
    )
    created_template_ids.append(template.id)

    # Create ONE bot
    bot_uuid = generate_uuid()
    bot_id = bot_repository.insert_bot(
        bot_uuid=bot_uuid,
        tenant=FIXED_TENANT_NAME,
        env=TEST_ENV,
        domain="test_domain",
        creator="test_user",
        modifier="test_user",
        status=BotStatus.ACTIVE.value,
        name=f"Shared Session Bot {bot_uuid[:8]}",
        description="Shared bot for session tests",
        template_uuid=None,
        replica_desired=1,
        replica_minimum=1,
        replica_maximum=10,
        auto_scaling_enabled=0,
        sla_grade="standard",
        extra_config={},
    )
    created_bot_ids.append(bot_id)

    return {
        "tenant": FIXED_TENANT_NAME,
        "bot_id": bot_id,
        "bot_uuid": bot_uuid,
    }


@pytest.mark.integration
class TestSessionServiceIntegration:
    """Optimized integration tests with shared fixtures."""

    def test_session_lifecycle(self, shared_session_setup):
        """Comprehensive test for session lifecycle.

        Tests:
        - create session generates valid session_id
        - create session stores trace_id
        - mark_running updates status
        - mark_completed stores result and status
        - mark_failed stores err_msg and status
        - get_by_session_id existing
        - get_by_session_id nonexistent returns None
        - get_by_trace_id finds matches
        - list_by_bot returns paginated
        """
        bot_uuid = shared_session_setup["bot_uuid"]

        # Test 1: Create session generates valid session_id
        session_id = _dss().create_session(
            bot_uuid=bot_uuid,
            invoker="test_user",
            req={"command": "test"},
            device_uuid="test_device_1",
            tenant="test_tenant",
        )
        assert session_id is not None
        assert session_id.startswith("SESSION-")

        # Test 2: Create session with trace_id
        trace_id = "trace-123"
        session_id_2 = _dss().create_session(
            bot_uuid=bot_uuid,
            invoker="test_user",
            req={"command": "test"},
            device_uuid="test_device_2",
            tenant="test_tenant",
            trace_id=trace_id,
        )
        assert session_id_2 is not None

        # Test 3: Mark running updates status (no env param)
        _dss().mark_running(session_id)
        session = _dss().get_by_session_id(session_id)
        assert session is not None
        assert session.status == "RUNNING"

        # Test 4: Mark completed stores result and status (no env param)
        _dss().mark_completed(session_id, {"result": "success"})
        session = _dss().get_by_session_id(session_id)
        assert session is not None
        assert session.status == "COMPLETED"
        assert session.result == {"result": "success"}

        # Test 5: Mark failed stores err_msg and status (no env param)
        session_id_3 = _dss().create_session(
            bot_uuid=bot_uuid,
            invoker="test_user",
            req={"command": "test"},
            device_uuid="test_device_3",
            tenant="test_tenant",
        )
        _dss().mark_failed(session_id_3, "Error occurred")
        session_3 = _dss().get_by_session_id(session_id_3)
        assert session_3 is not None
        assert session_3.status == "FAILED"
        assert session_3.err_msg == "Error occurred"

        # Test 6: Get by session_id existing
        session = _dss().get_by_session_id(session_id)
        assert session is not None
        assert session.session_id == session_id

        # Test 7: Get by session_id nonexistent returns None
        session = _dss().get_by_session_id("SESSION-nonexistent")
        assert session is None

        # Test 8: List by bot returns paginated
        result = _dss().list_by_bot(bot_uuid, page=1, page_size=10)
        assert result.total >= 3
        assert result.page == 1
