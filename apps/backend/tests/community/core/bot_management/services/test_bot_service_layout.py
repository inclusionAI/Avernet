"""Layout guard for the split ``bot_service.py`` (plan: Bot Avatar / Collaboration Repair)."""
from pathlib import Path
from agentclaw.community.core.bot_management.services import bot_service

def test_bot_service_source_is_bounded():
    root = Path(bot_service.__file__).parent
    paths = [Path(bot_service.__file__), *root.glob("bot_service_parts/*.py")]
    assert all(len(p.read_text().splitlines()) <= 1000 for p in paths)

def test_bot_service_public_surface_is_preserved():
    for name in ("create_bot", "update_bot", "start_bot", "restart_bot",
                 "delete_bot", "get_bot", "_sync_bot_to_bcn"):
        assert callable(getattr(bot_service.BotService, name))
    assert issubclass(bot_service.BotNotFoundError, bot_service.BotServiceError)
