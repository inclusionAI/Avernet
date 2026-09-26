from pathlib import Path


def test_temp_cache_base_falls_back_when_dev_shm_missing(monkeypatch):
    import agentclaw.community.core.skill_center.services.git_sync as gs
    monkeypatch.delenv("GIT_SYNC_TMP_BASE", raising=False)
    real_exists = Path.exists
    monkeypatch.setattr(Path, "exists",
        lambda self: False if str(self) == "/dev/shm" else real_exists(self))
    base = gs._resolve_temp_cache_base()
    assert base != Path("/dev/shm")
    assert base.is_dir()
