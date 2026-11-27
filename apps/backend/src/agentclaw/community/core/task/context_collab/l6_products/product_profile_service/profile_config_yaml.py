"""One strict YAML execution configuration, with original stages bytes preserved."""

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
    encode_composition,
    decode_composition,
)
from .stages_yaml import StrictLoader

SCHEMA = "product-profile/config@1"
MAX_CONFIG_BYTES = 65535  # MySQL TEXT capacity, measured after YAML serialization.


def encode_config(composition, stages: str) -> str:
    compile_configuration(composition, stages)
    value = yaml.safe_dump(
        {
            "schema": SCHEMA,
            "composition": encode_composition(composition),
            "stages": stages,
        },
        allow_unicode=True,
        sort_keys=False,
    )
    text(value, maximum=MAX_CONFIG_BYTES)
    return value


def decode_config(value: str):
    text(value, maximum=MAX_CONFIG_BYTES)
    try:
        data = yaml.load(value, Loader=StrictLoader)
    except (yaml.YAMLError, RecursionError, TypeError, ValueError):
        raise Invalid("invalid stored Profile YAML") from None
    shape(data, {"schema", "composition", "stages"})
    if data["schema"] != SCHEMA:
        raise Invalid("unsupported Profile YAML schema")
    composition = decode_composition(data["composition"])
    compile_configuration(composition, data["stages"])
    return composition, data["stages"]
