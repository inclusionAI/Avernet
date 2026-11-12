"""Manifest upsert conformance against the real config service and SQLite ORM."""
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.config.config import ConfigRepository
from agentclaw.community.core.system_config.orm import AcConfigCategory, AcConfigItem
from agentclaw.community.core.system_config.service import SystemConfigService
from agentclaw.community.core.task.task_panel.manifest import TaskPanelManifestService


@pytest.fixture
def storage(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'manifest.db'}")
    AcConfigCategory.__table__.create(engine)
    AcConfigItem.__table__.create(engine)
    sessions = sessionmaker(bind=engine)

    class Database:
        @contextmanager
        def orm_session(self):
            with sessions() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

    repository = ConfigRepository(Database())
    yield SystemConfigService(repository), repository
    engine.dispose()


def value(version):
    return {"schema_version": 1, "bundles": [{"name": "taskPanel", "type": "url",
        "url": f"https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@{version}/dist/index.umd.js"}]}


def test_create_replace_idempotence_environment_isolation_and_audit(storage):
    config, repository = storage
    service = TaskPanelManifestService(config)
    service.set_manifest(value=value("0.1.1"), env="pre", operator="alice")
    category = repository.get_category(category="task", env="pre")
    first = repository.get_config_by_key(parent_id=category.id, config_key="panel")
    assert first.creator == "alice" and category.creator == "alice"

    config.create_category(category="task", category_name="Existing name", env="pre", operator="alice")
    service.set_manifest(value=value("0.1.2"), env="pre", operator="bob")
    service.set_manifest(value=value("0.1.2"), env="pre", operator="bob")
    updated = repository.get_config_by_key(parent_id=category.id, config_key="panel")
    assert updated.id == first.id
    assert updated.creator == "alice" and updated.modifier == "bob"
    assert repository.get_category(category="task", env="pre").category_name == "Existing name"
    assert len(config.list_configs(category="task", env="pre")) == 1
    assert config.get_config(category="task", config_key="panel", env="pre") == value("0.1.2")

    service.set_manifest(value=value("0.1.1"), env="prod", operator="carol")
    # New service instances see persisted updates; no process cache or in-memory fake.
    reader = TaskPanelManifestService(SystemConfigService(repository))
    assert "@0.1.2/" in reader.get_manifest(env="pre").bundles[0].url
    assert "@0.1.1/" in reader.get_manifest(env="prod").bundles[0].url
    assert len(config.list_categories(env="pre")) == 1


def test_invalid_write_does_not_create_category_or_replace_existing_value(storage):
    config, _ = storage
    service = TaskPanelManifestService(config)
    with pytest.raises(ValueError):
        service.set_manifest(value={}, env="pre", operator="alice")
    assert config.list_categories(env="pre") == []
    service.set_manifest(value=value("0.1.1"), env="pre", operator="alice")
    with pytest.raises(ValueError):
        service.set_manifest(value={**value("0.1.2"), "env": "prod"}, env="pre", operator="alice")
    assert config.get_config(category="task", config_key="panel", env="pre") == value("0.1.1")
