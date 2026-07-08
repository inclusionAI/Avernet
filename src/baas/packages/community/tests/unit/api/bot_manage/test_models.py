"""Unit tests for api/bot_manager/_models.py — Bot management Pydantic models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from secbaas.api.bot_manage import (
    BotClusterCreate,
    BotConfig,
    BotListResponse,
    BotQuery,
    BotResponse,
    CreateBotResponse,
    DestroyBotResponse,
    RestartBotResponse,
    ScaleBotResponse,
    SlaGrade,
    UpdateBotResponse,
)
from secbaas.api.device_manage import DeployConfig


class TestBotConfig:
    """Tests for BotConfig model."""

    def test_defaults(self):
        """WHEN created with no args, THEN defaults are set."""
        config = BotConfig()
        assert config.share_policy is None
        assert config.sla_grade == SlaGrade.STANDARD
        assert config.deploy_config is None
        assert config.callback_timeout_seconds is None
        assert config.entity_id == ""
        assert config.entity_type == "staff"

    def test_valid_sla_grade(self):
        """WHEN sla_grade is valid, THEN model validates."""
        config = BotConfig(sla_grade="enterprise")
        assert config.sla_grade == "enterprise"

    def test_invalid_sla_grade(self):
        """WHEN sla_grade is invalid, THEN ValidationError raised."""
        with pytest.raises(ValidationError, match="sla_grade"):
            BotConfig(sla_grade="invalid")

    def test_callback_timeout_bounds(self):
        """WHEN callback_timeout_seconds exceeds 3600, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            BotConfig(callback_timeout_seconds=3601)

    def test_callback_timeout_zero(self):
        """WHEN callback_timeout_seconds is 0, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            BotConfig(callback_timeout_seconds=0)

    def test_valid_callback_timeout(self):
        """WHEN callback_timeout_seconds is valid, THEN model validates."""
        config = BotConfig(callback_timeout_seconds=300)
        assert config.callback_timeout_seconds == 300

    def test_extra_fields_allowed(self):
        """THEN extra fields are allowed (extra='allow')."""
        config = BotConfig(custom_field="value")
        assert config.custom_field == "value"

    def test_deploy_config(self):
        """WHEN deploy_config provided, THEN it is stored."""
        deploy = DeployConfig()
        config = BotConfig(deploy_config=deploy)
        assert config.deploy_config == deploy

    def test_entity_id_and_type(self):
        """WHEN entity_id and entity_type provided, THEN they are stored."""
        config = BotConfig(entity_id="e-123", entity_type="org")
        assert config.entity_id == "e-123"
        assert config.entity_type == "org"


class TestBotClusterCreate:
    """Tests for BotClusterCreate model."""

    def test_required_fields(self):
        """WHEN all required fields provided, THEN model validates."""
        req = BotClusterCreate(
            bot_name="my-bot",
            template_uuid="tpl-001",
            device_count=2,
            operator="user-123",
        )
        assert req.bot_name == "my-bot"
        assert req.template_uuid == "tpl-001"
        assert req.device_count == 2
        assert req.env == "prod"
        assert req.domain == "default"
        assert req.operator == "user-123"
        assert req.config is None

    def test_bot_name_too_short(self):
        """WHEN bot_name is empty, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            BotClusterCreate(
                bot_name="",
                template_uuid="tpl-001",
                device_count=1,
                operator="user",
            )

    def test_bot_name_too_long(self):
        """WHEN bot_name exceeds 1024 chars, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            BotClusterCreate(
                bot_name="x" * 1025,
                template_uuid="tpl-001",
                device_count=1,
                operator="user",
            )

    def test_device_count_ge_1(self):
        """WHEN device_count is 0, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            BotClusterCreate(
                bot_name="bot",
                template_uuid="tpl-001",
                device_count=0,
                operator="user",
            )

    def test_device_count_le_100(self):
        """WHEN device_count exceeds 100, THEN ValidationError raised."""
        with pytest.raises(ValidationError):
            BotClusterCreate(
                bot_name="bot",
                template_uuid="tpl-001",
                device_count=101,
                operator="user",
            )

    def test_optional_fields(self):
        """WHEN optional fields provided, THEN they are stored."""
        req = BotClusterCreate(
            bot_name="bot",
            template_uuid="tpl-001",
            device_count=1,
            operator="user",
            bot_desc="A test bot",
            env="staging",
            domain="test-domain",
            config=BotConfig(sla_grade="enterprise"),
        )
        assert req.bot_desc == "A test bot"
        assert req.env == "staging"
        assert req.domain == "test-domain"
        assert req.config.sla_grade == "enterprise"


class TestBotResponse:
    """Tests for BotResponse model."""

    def test_required_fields(self):
        """WHEN all required fields provided, THEN model validates."""
        now = datetime.now()
        resp = BotResponse(
            id=1,
            bot_uuid="uuid-123",
            tenant="t1",
            env="prod",
            domain="default",
            is_deleted=0,
            creator="user-1",
            modifier="user-1",
            status="ACTIVE",
            name="bot-name",
            description=None,
            template_uuid="tpl-001",
            replica_desired=2,
            replica_minimum=1,
            replica_maximum=5,
            auto_scaling_enabled=0,
            sla_grade="standard",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.bot_uuid == "uuid-123"
        assert resp.status == "ACTIVE"
        assert resp.name == "bot-name"

    def test_optional_devices(self):
        """WHEN devices omitted, THEN defaults to empty list."""
        now = datetime.now()
        resp = BotResponse(
            id=1,
            bot_uuid="u-1",
            tenant="t",
            env="p",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="A",
            name="n",
            description=None,
            template_uuid="tpl",
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=1,
            auto_scaling_enabled=0,
            sla_grade="standard",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.devices == []

    def test_from_attributes(self):
        """THEN model_config has from_attributes=True for ORM compat."""
        assert BotResponse.model_config.get("from_attributes") is True


class TestBotListResponse:
    """Tests for BotListResponse model."""

    def test_required_fields(self):
        """WHEN created with items and pagination, THEN all set."""
        resp = BotListResponse(items=[], total=0, page=1, page_size=20)
        assert resp.items == []
        assert resp.total == 0

    def test_default_page_size(self):
        """WHEN defaults provided, THEN page=1, page_size=20."""
        now = datetime.now()
        bot = BotResponse(
            id=1,
            bot_uuid="u",
            tenant="t",
            env="p",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="A",
            name="n",
            description=None,
            template_uuid="tpl",
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=1,
            auto_scaling_enabled=0,
            sla_grade="standard",
            gmt_create=now,
            gmt_modified=now,
        )
        resp = BotListResponse(items=[bot], total=1, page=1, page_size=20)
        assert len(resp.items) == 1
        assert resp.items[0].bot_uuid == "u"


class TestBotQuery:
    """Tests for BotQuery model."""

    def test_defaults(self):
        """WHEN created with no args, THEN defaults are set."""
        q = BotQuery()
        assert q.entity_id is None
        assert q.is_delete == 0
        assert q.env is None

    def test_all_fields(self):
        """WHEN all fields provided, THEN they are stored."""
        q = BotQuery(
            entity_id="e-1",
            entity_type="staff",
            creator_id="c-1",
            owner_id="o-1",
            status="ACTIVE",
            is_delete=1,
            env="prod",
            public="1",
        )
        assert q.entity_id == "e-1"
        assert q.status == "ACTIVE"
        assert q.is_delete == 1
        assert q.public == "1"


class TestUpdateBotResponse:
    """Tests for UpdateBotResponse model."""

    def test_inherits_bot_response(self):
        """THEN UpdateBotResponse is a BotResponse subclass."""
        assert issubclass(UpdateBotResponse, BotResponse)

    def test_optional_publish_id(self):
        """WHEN publish_id omitted, THEN defaults to None."""
        now = datetime.now()
        resp = UpdateBotResponse(
            id=1,
            bot_uuid="u",
            tenant="t",
            env="p",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="A",
            name="n",
            description=None,
            template_uuid="tpl",
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=1,
            auto_scaling_enabled=0,
            sla_grade="standard",
            gmt_create=now,
            gmt_modified=now,
        )
        assert resp.publish_id is None

    def test_with_publish_id(self):
        """WHEN publish_id provided, THEN it is stored."""
        now = datetime.now()
        resp = UpdateBotResponse(
            id=1,
            bot_uuid="u",
            tenant="t",
            env="p",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="A",
            name="n",
            description=None,
            template_uuid="tpl",
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=1,
            auto_scaling_enabled=0,
            sla_grade="standard",
            gmt_create=now,
            gmt_modified=now,
            publish_id=42,
        )
        assert resp.publish_id == 42


class TestCreateBotResponse:
    """Tests for CreateBotResponse model."""

    def test_inherits_bot_response_and_with_request_id(self):
        """THEN CreateBotResponse inherits from both BotResponse and WithRequestId."""
        assert issubclass(CreateBotResponse, BotResponse)


class TestRestartBotResponse:
    """Tests for RestartBotResponse model."""

    def test_required_publish_id(self):
        """WHEN publish_id omitted, THEN ValidationError raised."""
        now = datetime.now()
        with pytest.raises(ValidationError):
            RestartBotResponse(
                id=1,
                bot_uuid="u",
                tenant="t",
                env="p",
                domain="d",
                is_deleted=0,
                creator="c",
                modifier="m",
                status="A",
                name="n",
                description=None,
                template_uuid="tpl",
                replica_desired=1,
                replica_minimum=1,
                replica_maximum=1,
                auto_scaling_enabled=0,
                sla_grade="standard",
                gmt_create=now,
                gmt_modified=now,
            )


class TestScaleBotResponse:
    """Tests for ScaleBotResponse model."""

    def test_required_fields(self):
        """WHEN all fields provided, THEN model validates."""
        now = datetime.now()
        resp = ScaleBotResponse(
            id=1,
            bot_uuid="u",
            tenant="t",
            env="p",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="A",
            name="n",
            description=None,
            template_uuid="tpl",
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=1,
            auto_scaling_enabled=0,
            sla_grade="standard",
            gmt_create=now,
            gmt_modified=now,
            request_id="a" * 32,
            target_count=3,
            publish_id=42,
        )
        assert resp.target_count == 3
        assert resp.publish_id == 42


class TestDestroyBotResponse:
    """Tests for DestroyBotResponse model."""

    def test_required_fields(self):
        """WHEN all fields provided, THEN model validates."""
        now = datetime.now()
        resp = DestroyBotResponse(
            id=1,
            bot_uuid="u",
            tenant="t",
            env="p",
            domain="d",
            is_deleted=0,
            creator="c",
            modifier="m",
            status="A",
            name="n",
            description=None,
            template_uuid="tpl",
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=1,
            auto_scaling_enabled=0,
            sla_grade="standard",
            gmt_create=now,
            gmt_modified=now,
            request_id="a" * 32,
            publish_id=99,
        )
        assert resp.publish_id == 99
