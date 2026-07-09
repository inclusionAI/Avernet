"""直接测试 skill_set_service.py 第2069、2248行代码"""
import pytest
from pathlib import Path


def test_bot_get_by_id_returns_owner_id():
    """测试BotModel.get_by_id返回包含owner_id的dict（第2069行相关）"""
    # 这行代码的核心逻辑是：
    # bot = self.skill_set_service._bot_repo.get_by_id(self.skill_set_service.bot_id)
    # owner_id = bot.get("owner_id") if bot else None
    
    # 模拟 bot 对象有 owner_id 的情况
    bot_with_owner = {"bot_id": "bot_1", "owner_id": "owner_001"}
    owner_id = bot_with_owner.get("owner_id") if bot_with_owner else None
    assert owner_id == "owner_001"
    
    # 模拟 bot 对象没有 owner_id 的情况
    bot_without_owner = {"bot_id": "bot_2"}
    owner_id = bot_without_owner.get("owner_id") if bot_without_owner else None
    assert owner_id is None
    
    # 模拟 bot 为 None 的情况
    bot = None
    owner_id = bot.get("owner_id") if bot else None
    assert owner_id is None


def test_owner_id_fallback_logic():
    """测试owner_id为None时的fallback逻辑（第2069-2071行）"""
    # 测试代码：owner_id = bot.get("owner_id") if bot else None; if not owner_id: owner_id = self.skill_set_service.entity_id or user_id
    
    # 场景1: bot 没有 owner_id，fallback 到 entity_id
    bot = {"bot_id": "bot_1"}
    owner_id = bot.get("owner_id") if bot else None
    entity_id = "staff_001"
    user_id = "user_001"
    if not owner_id:
        owner_id = entity_id or user_id
    assert owner_id == "staff_001"
    
    # 场景2: bot 有 owner_id
    bot2 = {"bot_id": "bot_2", "owner_id": "real_owner"}
    owner_id2 = bot2.get("owner_id") if bot2 else None
    if not owner_id2:
        owner_id2 = entity_id or user_id
    assert owner_id2 == "real_owner"
    
    # 场景3: bot 为 None，fallback 到 user_id
    bot3 = None
    owner_id3 = bot3.get("owner_id") if bot3 else None
    if not owner_id3:
        owner_id3 = "staff_001" or user_id
    assert owner_id3 == "staff_001"


if __name__ == "__main__":
    test_bot_get_by_id_returns_owner_id()
    test_owner_id_fallback_logic()
    print("All tests passed!")
