"""Strict nested YAML Profile config with a readable structured stage graph."""

import yaml

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    shape,
    text,
)
from .configuration import (
    compile_configuration,
    decode_composition,
    encode_composition,
)
from .stages_yaml import StrictLoader, compile_stages

SCHEMA = "product-profile/config@2"
LEGACY_SCHEMA = "product-profile/config@1"
MAX_CONFIG_BYTES = 65535  # MySQL TEXT capacity, measured after YAML serialization.


def stages_config(stages: str) -> list[dict]:
    """Normalize accepted API YAML into the storage-native stage graph."""
    results = []
    for spec in compile_stages(stages):
        results.append(
            {
                "name": spec.name,
                "candidates": [
                    {
                        "type": candidate.agent_type,
                        "uuid": candidate.agent_id,
                    }
                    for candidate in spec.candidates
                ],
                "depends_on": list(spec.depends_on),
                "acceptance": [
                    {"condition": condition} for condition in spec.conditions
                ],
            }
        )
    return results


def encode_config(composition, stages: str) -> str:
    compile_configuration(composition, stages)
    value = yaml.safe_dump(
        {
            "schema": SCHEMA,
            "composition": encode_composition(composition),
            "stages": stages_config(stages),
        },
        allow_unicode=True,
        sort_keys=False,
    )
    text(value, maximum=MAX_CONFIG_BYTES)
    return value


def _read_config(value: str):
    text(value, maximum=MAX_CONFIG_BYTES)
    try:
        data = yaml.load(value, Loader=StrictLoader)
    except (yaml.YAMLError, RecursionError, TypeError, ValueError):
        raise Invalid("invalid stored Profile YAML") from None
    shape(data, {"schema", "composition", "stages"})
    return data


def decode_config(value: str):
    data = _read_config(value)
    schema, stored_stages = data["schema"], data["stages"]
    if schema == SCHEMA:
        if type(stored_stages) is not list:
            raise Invalid("stored Profile stages must be a YAML list")
        stages = yaml.safe_dump(
            {"stages": stored_stages},
            allow_unicode=True,
            sort_keys=False,
        )
    elif schema == LEGACY_SCHEMA:
        if type(stored_stages) is not str:
            raise Invalid("legacy Profile stages must be a string")
        stages = stored_stages
    else:
        raise Invalid("unsupported Profile YAML schema")
    composition = decode_composition(data["composition"])
    compile_configuration(composition, stages)
    return composition, stages
