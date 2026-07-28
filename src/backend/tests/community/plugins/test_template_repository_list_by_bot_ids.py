from agentclaw.community.plugins.template_repository import TemplateRepository


def test_list_by_bot_ids_empty_returns_empty_without_db():
    repo = TemplateRepository.__new__(TemplateRepository)

    assert repo.list_by_bot_ids([]) == []


def test_list_by_bot_ids_blank_values_return_empty_without_db():
    repo = TemplateRepository.__new__(TemplateRepository)

    assert repo.list_by_bot_ids(["", None]) == []  # type: ignore[list-item]
