import pytest
from pydantic import ValidationError
from search_core.config.document_mapping import DocumentMapping, MappingError, render


def test_template_lists_literals_and_filter_arrays():
    rule = DocumentMapping(
        search_text_template="{{助手}} *{name}*\n{skills[].description}",
        rerank_text_template="{missing}",
        filter_fields={"capability": "skills[].tags[]"},
    )
    card = {
        "name": "账单",
        "skills": [
            {"description": "退款", "tags": ["中文 / ⭐", "重复"]},
            {"description": "退款", "tags": ["重复", " "]},
        ],
    }
    result = render(rule, card)
    assert result.search_text == "{助手} *账单*\n退款\n退款"
    assert result.rerank_text == result.search_text
    assert result.filters == {"capability": ("中文 / ⭐", "重复")}
    assert result.warnings[0]["path"] == "missing"


@pytest.mark.parametrize(
    "template", ["{name", "{skills[0].name}", "{name!r}", "{name:>8}", "{}"]
)
def test_invalid_templates_have_field_location(template):
    with pytest.raises(ValidationError) as error:
        DocumentMapping(search_text_template=template)
    assert error.value.errors()[0]["loc"][0] == "search_text_template"


def test_type_error_points_to_actual_card_element():
    rule = DocumentMapping(
        search_text_template="{name}", filter_fields={"capability": "skills[].tags[]"}
    )
    with pytest.raises(MappingError) as error:
        render(rule, {"name": "x", "skills": [{"tags": "bad"}]})
    assert error.value.location == ("filter_fields", "capability")
    assert error.value.card_path == "skills[0].tags"


def test_reserved_filters_and_empty_text_rejected():
    with pytest.raises(ValidationError):
        DocumentMapping(filter_fields={"resource_type": "name"})
    with pytest.raises(MappingError):
        render(DocumentMapping(search_text_template="{missing}"), {})


def test_partial_override_inherits_global_and_old_index_ignores_new_filter_keys():
    from search_core.config.document_mapping import effective_mapping

    base = DocumentMapping(
        search_text_template="{name}",
        rerank_text_template="{description}",
        filter_fields={"category": "tags[]", "region": "provider.region"},
    )
    override = {
        "rerank_text_template": "{name}",
        "filter_fields": {"category": "category"},
    }
    merged = effective_mapping(base, override, validate_keys=True)
    result = render(
        merged, {"name": "n", "category": "a", "provider": {"region": "cn"}}
    )
    assert result.search_text == result.rerank_text == "n"
    assert result.filters == {"category": ("a",), "region": ("cn",)}
    with pytest.raises(MappingError, match="declared in the global"):
        effective_mapping(None, override, validate_keys=True)
    legacy = effective_mapping(None, override)
    assert legacy.filter_fields == {}  # Old index has no newly declared filters.
    assert legacy.rerank_text_template == "{name}"


def test_scalar_rendering_missing_paths_and_nonstring_filters():
    rule = DocumentMapping(search_text_template="{count}/{enabled}/{nullable}/{absent}")
    result = render(rule, {"count": 0, "enabled": False, "nullable": None})
    assert result.search_text == "0/false//"
    assert {warning["path"] for warning in result.warnings} == {"nullable", "absent"}
    with pytest.raises(MappingError, match="expected a string"):
        render(
            DocumentMapping(
                search_text_template="text", filter_fields={"category": "count"}
            ),
            {"count": 1},
        )
