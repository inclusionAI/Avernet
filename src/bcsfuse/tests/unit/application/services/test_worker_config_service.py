"""
Tests for WorkerConfig model and store-level batch_get_configs

覆盖范围：
1. WorkerConfig 模型（默认值、类型校验、extra forbid）
2. Worker 模型集成（config 字段默认值、读写）
3. Store 层 batch_get_configs 批量查询
4. Store 层 round-trip（config JSON 序列化/反序列化）
5. ParticipantAvailabilityChecker fusion_enable 集成
"""

import pytest

from src.domain.models.worker import (
    Worker,
    WorkerType,
    WorkerIdentity,
    WorkerState,
    Availability,
    TrustLevel,
    Capability,
    CapabilityLevel,
)


# ============================================================================
# 1. WorkerConfig 模型测试
# ============================================================================


class TestWorkerConfigModel:
    """WorkerConfig Pydantic 模型测试"""

    def test_default_fusion_enable_is_false(self):
        """默认 fusion_enable=False，需显式开启融合"""
        from src.domain.models.worker_config import WorkerConfig
        config = WorkerConfig()
        assert config.fusion_enable is False

    def test_explicit_fusion_enable_true(self):
        from src.domain.models.worker_config import WorkerConfig
        config = WorkerConfig(fusion_enable=True)
        assert config.fusion_enable is True

    def test_extra_field_forbidden(self):
        """extra='forbid' 禁止未知字段"""
        from src.domain.models.worker_config import WorkerConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            WorkerConfig(unknown_field=1)

    def test_model_dump_round_trip(self):
        from src.domain.models.worker_config import WorkerConfig
        config = WorkerConfig(fusion_enable=True)
        data = config.model_dump()
        assert data == {"fusion_enable": True}
        restored = WorkerConfig(**data)
        assert restored.fusion_enable is True


# ============================================================================
# 2. Worker 模型集成测试
# ============================================================================


class TestWorkerConfigIntegration:
    """Worker 模型 config 字段集成"""

    def test_worker_default_config(self):
        """Worker 默认 config.fusion_enable=False"""
        worker = Worker(
            id="wrk_test",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Test", handle="@test"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.GUARDED),
        )
        assert worker.config.fusion_enable is False

    def test_worker_explicit_config(self):
        """Worker 可显式设置 config"""
        from src.domain.models.worker_config import WorkerConfig
        worker = Worker(
            id="wrk_test",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Test", handle="@test"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.GUARDED),
            config=WorkerConfig(fusion_enable=True),
        )
        assert worker.config.fusion_enable is True

    def test_worker_config_is_mutable(self):
        """Worker config 可直接修改"""
        worker = Worker(
            id="wrk_test",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Test", handle="@test"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.GUARDED),
        )
        worker.config = worker.config.model_copy(update={"fusion_enable": True})
        assert worker.config.fusion_enable is True


# ============================================================================
# 3. Store 层 batch_get_configs 测试
# ============================================================================


class TestBatchGetConfigs:
    """Store 层 batch_get_configs 方法测试"""

    @pytest.fixture
    def store(self):
        from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
        return InMemoryWorkerRegistryStore()

    def test_batch_get_configs_mixed(self, store):
        """批量查询：部分开启、部分关闭、部分不存在"""
        from src.domain.models.worker_config import WorkerConfig
        worker_a = Worker(
            id="wrk_a",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="A", handle="@a"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.GUARDED),
            config=WorkerConfig(fusion_enable=True),
        )
        worker_b = Worker(
            id="wrk_b",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="B", handle="@b"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.GUARDED),
        )
        store.create(worker_a)
        store.create(worker_b)

        configs, not_found = store.batch_get_configs(["wrk_a", "wrk_b", "wrk_c"])

        assert configs["wrk_a"].fusion_enable is True
        assert configs["wrk_b"].fusion_enable is False
        assert "wrk_c" in not_found

    def test_batch_get_configs_empty_ids(self, store):
        """空 ID 列表返回空结果"""
        configs, not_found = store.batch_get_configs([])
        assert configs == {}
        assert not_found == []

    def test_batch_get_configs_all_not_found(self, store):
        """全部不存在的 ID"""
        configs, not_found = store.batch_get_configs(["x1", "x2"])
        assert configs == {}
        assert set(not_found) == {"x1", "x2"}


# ============================================================================
# 4. Store 层 round-trip 测试
# ============================================================================


class TestWorkerConfigStoreRoundTrip:
    """Store 层 config JSON 序列化/反序列化测试"""

    @pytest.fixture
    def store(self):
        from src.infra.adapters.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
        return SQLiteWorkerRegistryStore(":memory:")

    def test_create_and_read_worker_with_config(self, store):
        """创建带 config 的 Worker，读回值一致"""
        from src.domain.models.worker_config import WorkerConfig
        worker = Worker(
            id="wrk_fuse_test",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Fuse Bot", handle="@fuse-bot"),
            responsibilities=["fusion"],
            capabilities=[Capability(name="fuse", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PUBLIC, trust_level=TrustLevel.TRUSTED),
            config=WorkerConfig(fusion_enable=True),
        )
        store.create(worker)
        loaded = store.get_by_id("wrk_fuse_test")
        assert loaded is not None
        assert loaded.config.fusion_enable is True

    def test_create_and_read_worker_without_config(self, store):
        """默认 Worker（未设 config）读回 fusion_enable=False"""
        worker = Worker(
            id="wrk_no_config",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="No Config", handle="@no-config"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.GUARDED),
        )
        store.create(worker)
        loaded = store.get_by_id("wrk_no_config")
        assert loaded is not None
        assert loaded.config.fusion_enable is False

    def test_update_config_and_read(self, store):
        """更新 config 后读回值一致"""
        from src.domain.models.worker_config import WorkerConfig
        worker = Worker(
            id="wrk_update_config",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Update Config", handle="@update-config"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PRIVATE, trust_level=TrustLevel.GUARDED),
        )
        created = store.create(worker)

        # 更新 config
        created.config = WorkerConfig(fusion_enable=True)
        updated = store.update(created)

        loaded = store.get_by_id("wrk_update_config")
        assert loaded.config.fusion_enable is True
        assert loaded.version == 2


# ============================================================================
# 5. fusion_enable 不影响 availability 判定，由融合流程自行检查
# ============================================================================


class TestParticipantAvailabilityIgnoreFusionEnable:
    """fusion_enable 不影响 availability 判定"""

    @pytest.fixture
    def checker(self):
        from src.application.services.participant_availability_checker import ParticipantAvailabilityChecker
        from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
        from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
        from src.infra.adapters.in_memory_worker_profile_binding_store import InMemoryWorkerProfileBindingStore

        registry_store = InMemoryWorkerRegistryStore()
        runtime_state_store = InMemoryWorkerRuntimeStateStore()
        profile_binding_store = InMemoryWorkerProfileBindingStore()

        # 设置 feature flag
        import os
        os.environ["ENABLE_EXPLICIT_PARTICIPANT_AVAILABILITY_WARNING"] = "true"

        return ParticipantAvailabilityChecker(
            profile_binding_store=profile_binding_store,
            runtime_state_store=runtime_state_store,
            registry_store=registry_store,
        )

    def test_fusion_disable_but_online_still_available(self, checker):
        """fusion_enable=False 但 online 的 worker，availability 仍为 True"""
        from src.domain.models.worker_config import WorkerConfig
        from src.domain.models.worker_runtime_state import WorkerRuntimeState

        registry_store = checker._registry_store
        worker = Worker(
            id="wrk_fuse_off",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Fuse Off", handle="@fuse-off"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PUBLIC, trust_level=TrustLevel.TRUSTED),
            config=WorkerConfig(fusion_enable=False),
        )
        registry_store.create(worker)
        checker._runtime_state_store.set_runtime_state("wrk_fuse_off", WorkerRuntimeState.ONLINE)

        result = checker.check_availability("wrk_fuse_off")
        assert result.is_available is True

    def test_fusion_enable_online_available(self, checker):
        """fusion_enable=True 且 online 的 worker，availability 为 True"""
        from src.domain.models.worker_config import WorkerConfig
        from src.domain.models.worker_runtime_state import WorkerRuntimeState

        registry_store = checker._registry_store
        worker = Worker(
            id="wrk_fuse_on",
            type=WorkerType.BOT,
            identity=WorkerIdentity(name="Fuse On", handle="@fuse-on"),
            responsibilities=["test"],
            capabilities=[Capability(name="test", level=CapabilityLevel.EXPERT)],
            state=WorkerState(availability=Availability.PUBLIC, trust_level=TrustLevel.TRUSTED),
            config=WorkerConfig(fusion_enable=True),
        )
        registry_store.create(worker)
        checker._runtime_state_store.set_runtime_state("wrk_fuse_on", WorkerRuntimeState.ONLINE)

        result = checker.check_availability("wrk_fuse_on")
        assert result.is_available is True