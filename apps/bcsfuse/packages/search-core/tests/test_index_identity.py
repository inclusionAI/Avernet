"""Version names are storage identities; hashes only identify legacy data."""

import pytest
from search_core.catalog.updates import configuration_index_key
from search_core.config.index_builds import IndexBuildConfig


def test_legacy_key_stays_readable():
    assert (
        configuration_index_key(
            "Qwen3-Embedding-8B/4096",
            {"resource_type"},
            conversion_version="agent-card-v3",
        )
        == "421c4ed5e1b2079c621010ad6ed8cc1403ab916e49a9d0a0303f5c691cd3a037"
    )


@pytest.mark.parametrize("name", ["default", "embedding-v2"])
def test_version_name_is_index_key(name):
    cfg = IndexBuildConfig(
        build_id=name,
        embedding_model="model",
        embedding_dimension=2,
        conversion_version="rule-v1",
        filter_fields=("region", "category", "region"),
    )
    assert cfg.index_key == name
    assert cfg.filter_fields == ("category", "region")
