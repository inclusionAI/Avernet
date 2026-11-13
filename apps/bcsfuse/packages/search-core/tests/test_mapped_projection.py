import pytest
from search_core.config.document_mapping import DocumentMapping, MappingError
from search_core.plugin_api.card_documents import CardProjection
from search_core.plugin_api.index_documents import DocumentText


def test_partial_mapping_preserves_default_documents_and_filters():
    from search_core.catalog.mapping import map_projection

    default = CardProjection(
        (DocumentText("0", "first"), DocumentText("1", "second")),
        {"runtime_state": ("online",)},
    )
    mapped, warnings = map_projection(
        {"name": "raw"}, DocumentMapping(rerank_text_template="{name}"), default
    )
    assert [(d.key, d.search_text, d.rerank_text) for d in mapped.documents] == [
        ("0", "first", "raw"),
        ("1", "second", "raw"),
    ]
    assert mapped.filters == default.filters
    assert not warnings


def test_explicit_search_mapping_replaces_default_fragments():
    from search_core.catalog.mapping import map_projection

    default = CardProjection(
        (DocumentText("0", "derived"), DocumentText("1", "old")), {}
    )
    mapped, _ = map_projection(
        {"description": "original"},
        DocumentMapping(search_text_template="{description}"),
        default,
    )
    assert list(mapped.documents) == [DocumentText("0", "original", "original")]


def test_mapping_cannot_override_protected_business_filters():
    from search_core.catalog.mapping import map_projection

    with pytest.raises(MappingError, match="protected"):
        map_projection(
            {"name": "private"},
            DocumentMapping(filter_fields={"availability": "name"}),
            CardProjection((DocumentText("0", "text"),), {"availability": ("public",)}),
            protected_filter_fields={"availability"},
        )
