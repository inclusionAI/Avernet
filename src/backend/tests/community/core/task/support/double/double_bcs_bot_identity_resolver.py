"""测试用 BCS Bot 身份解析器。"""
from __future__ import annotations


class _DoubleBcsBotIdentityResolver:
    def __init__(self, owner_id: str = "double-owner") -> None:
        self._owner_id = owner_id

    def resolve_many(self, product_bot_ids: list[str]) -> dict[str, str]:
        return {bot_id: f"{bot_id}:{self._owner_id}" for bot_id in product_bot_ids}
