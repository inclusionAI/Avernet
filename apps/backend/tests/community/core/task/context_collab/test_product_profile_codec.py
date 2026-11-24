from dataclasses import replace
import json
import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_codec import (
    encode_profile,
    decode_profile,
    profile_digest,
)
from agentclaw.community.di.modules.context_collab_products import (
    INSTALLED_PRODUCT_PROFILES,
)
from tests.community.core.task.context_collab.swarm_workflow_support import profile


@pytest.mark.parametrize("definition", (*INSTALLED_PRODUCT_PROFILES, profile()))
def test_complete_definition_codec_roundtrip(definition):
    text = encode_profile(definition)
    assert decode_profile(text) == definition
    assert encode_profile(decode_profile(text)) == text
    assert len(profile_digest(definition)) == 64
    methods = json.loads(text)["definition"]["composition"]["required_methods"]
    assert set(methods[0]["profile"]) == {"type", "method", "version"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version=True),
        lambda d: d.update(schema_version=2),
        lambda d: d.update(unknown=1),
        lambda d: d["definition"]["ref"].update(version=True),
        lambda d: d["definition"]["ref"].update(version=0),
        lambda d: d["definition"]["composition"]["required_methods"][0][
            "profile"
        ].update(capabilities=[]),
        lambda d: d["definition"]["composition"]["required_methods"][0][
            "policy"
        ].update(type="evil.Import"),
        lambda d: d["definition"]["composition"]["required_methods"][0][
            "profile"
        ].update(version="latest"),
    ],
)
def test_malformed_profile_definitions_rejected(mutate):
    data = json.loads(encode_profile(profile()))
    mutate(data)
    with pytest.raises(ContextCollabValidationError):
        decode_profile(json.dumps(data))


@pytest.mark.parametrize(
    "text",
    [
        '{"schema_version":1,"schema_version":1,"definition":{}}',
        '{"schema_version":NaN,"definition":{}}',
        '{"schema_version":Infinity,"definition":{}}',
        "x" * 1048577,
    ],
)
def test_duplicate_nonfinite_oversized_json_rejected(text):
    with pytest.raises(ContextCollabValidationError):
        decode_profile(text)


def test_db_cannot_override_installed_method_contract():
    definition = profile()
    template = definition.composition.required_methods[0]
    forged = replace(
        template, profile=replace(template.profile, capabilities=("forged",))
    )
    with pytest.raises(ContextCollabValidationError):
        encode_profile(
            replace(
                definition,
                composition=replace(definition.composition, required_methods=(forged,)),
            )
        )
