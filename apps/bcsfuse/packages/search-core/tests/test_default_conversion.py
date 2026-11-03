"""Public default projection and business replacement share one Doc pipeline."""

import importlib.util

import pytest
from search_core.catalog.cards import CardSnapshot
from search_core.plugin_api.card_documents import CardProjection


def converter_type():
    assert importlib.util.find_spec("search_core.catalog.conversion"), (
        "a public default Card converter is required"
    )
    from search_core.catalog.conversion import DefaultCardDocumentConverter

    return DefaultCardDocumentConverter


def test_default_uses_semantic_fields_and_explicit_filter_labels():
    converter = converter_type()(filter_fields=frozenset({"category", "resource_type"}))
    card = CardSnapshot(
        agent_id="nutrition",
        source_version="1",
        source_card={
            "name": "营养助手",
            "description": "解读食品营养标签",
            "skills": [
                {"name": "饮食规划", "description": "安排每日饮食", "tags": ["健康"]}
            ],
            "category": "health",
            "supportedInterfaces": [{"url": "https://example.com/a2a"}],
            "metadata": {"private_note": "do-not-index"},
        },
    )
    before = card.model_dump()
    result = converter.convert(card)
    assert isinstance(result, CardProjection) and len(result.documents) == 1
    doc = result.documents[0]
    assert all(
        value in doc.search_text
        for value in ("营养助手", "营养标签", "饮食规划", "健康")
    )
    assert (
        "example.com" not in doc.search_text and "do-not-index" not in doc.search_text
    )
    assert result.filters == {"category": ("health",), "resource_type": ("agent",)}
    assert doc.rerank_text == doc.search_text
    assert card.model_dump() == before


def test_missing_optional_fields_do_not_block_conversion():
    converter = converter_type()()
    card = CardSnapshot(
        agent_id="one", source_version="1", source_card={"name": "Only name"}
    )
    assert "Only name" in converter.convert(card).documents[0].search_text
    assert converter.version


def test_empty_semantic_content_fails_conversion_without_indexing_arbitrary_json():
    converter = converter_type()()
    card = CardSnapshot(
        agent_id="one", source_version="1", source_card={"unrelated": "secret"}
    )
    with pytest.raises(ValueError):
        converter.convert(card)
