"""Explicit storage routing remains separate from the business tenant."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from secbaas.community.api.device_manage import ArcaCredentials, ArcaDeviceConfig
from secbaas.community.api.template_manage import ArcaTemplateConfig
from secbaas.community.core.service.paas import PaasServiceFactory


def config(**extra):
    return ArcaTemplateConfig(
        type="ARCA", base_url="https://arca.example.com", api_key="test-key", **extra
    )


@pytest.mark.parametrize("value", [None, "storage-tenant_1"])
def test_factory_propagates_storage_tenant_without_business_tenant_inference(value):
    template = MagicMock(
        config=config(arca_tenant_id=value),
        tenant="business-tenant",
        template_id=42,
        template_uuid="template",
    )
    factory = PaasServiceFactory.__new__(PaasServiceFactory)
    creds = factory._create_arca_credentials_from_template(template)
    assert creds.arca_tenant_id == value
    assert creds.tenant_name == "business-tenant"
    assert ArcaCredentials.model_validate(creds.model_dump()).arca_tenant_id == value


@pytest.mark.parametrize(
    "value", ["", " ", "tenant\r\ninjected", " tenant", "/tenant", 123, "a" * 129]
)
def test_invalid_routing_tenant_rejected_at_both_boundaries(value):
    with pytest.raises(ValidationError):
        config(arca_tenant_id=value)
    with pytest.raises(ValidationError):
        ArcaCredentials(template_id=42, template_uuid="template", arca_tenant_id=value)


def test_old_json_and_schema_default_disable_optional_recovery():
    assert config().arca_tenant_id is None
    schema = ArcaTemplateConfig.model_json_schema()
    assert schema["properties"]["arca_tenant_id"]["default"] is None
    assert "arca_tenant_id" not in schema["required"]


def test_detail_config_cannot_inject_credential():
    detail = ArcaDeviceConfig.model_validate({"arca_tenant_id": "untrusted"})
    assert "arca_tenant_id" not in detail.to_create_config()


@pytest.mark.parametrize("key", ["arca_template_id", "template_id"])
def test_existing_template_id_inputs_and_serialization_alias_preserved(key):
    template = config(**{key: "existing-template"})
    assert template.arca_template_id == "existing-template"
    assert template.model_dump()["arca_template_id"] == "existing-template"
    assert template.model_dump(by_alias=True)["template_id"] == "existing-template"
