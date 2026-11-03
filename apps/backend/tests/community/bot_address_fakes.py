"""Adapt narrow existing Bot doubles to the legacy-locator repository contract."""


def support_legacy_locator(repository):
    def matches(bot_id, locator):
        row = repository.get_by_id_and_owner(bot_id, locator)
        if row is None:
            return []
        if not isinstance(row, dict):
            raise AssertionError("Configure the Bot double with a persisted Bot row")
        for key, value in {
            "id": 1,
            "bot_id": bot_id,
            "entity_id": locator,
            "owner_id": locator,
        }.items():
            row.setdefault(key, value)
        return [row]

    repository.list_matches_by_legacy_locator.side_effect = matches
    return repository
