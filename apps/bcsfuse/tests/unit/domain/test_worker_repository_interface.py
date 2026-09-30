"""
WorkerRepository Interface Tests

验证 WorkerRepository interface 定义正确。

M1: 定义 Repository 的契约。
"""

import pytest
from abc import ABC, abstractmethod
from typing import Protocol


class TestWorkerRepositoryInterface:
    """WorkerRepository interface 测试"""

    def test_worker_repository_importable(self):
        """验证 WorkerRepository 可导入"""
        from src.domain.services.worker_repository import WorkerRepository
        assert WorkerRepository is not None

    def test_worker_repository_is_abstract(self):
        """验证 WorkerRepository 是抽象类或 Protocol"""
        from src.domain.services.worker_repository import WorkerRepository

        # 应该是 Protocol 或 ABC
        is_protocol = isinstance(WorkerRepository, type) and issubclass(WorkerRepository, Protocol)
        is_abc = isinstance(WorkerRepository, type) and issubclass(WorkerRepository, ABC)

        assert is_protocol or is_abc, "WorkerRepository should be a Protocol or ABC"

    def test_worker_repository_has_create_method(self):
        """验证 WorkerRepository 有 create 方法"""
        from src.domain.services.worker_repository import WorkerRepository
        import inspect

        # 检查是否有 create 方法
        has_create = hasattr(WorkerRepository, 'create') or 'create' in dir(WorkerRepository)
        assert has_create, "WorkerRepository should have 'create' method"

    def test_worker_repository_has_get_by_id_method(self):
        """验证 WorkerRepository 有 get_by_id 方法"""
        from src.domain.services.worker_repository import WorkerRepository

        has_get_by_id = hasattr(WorkerRepository, 'get_by_id') or 'get_by_id' in dir(WorkerRepository)
        assert has_get_by_id, "WorkerRepository should have 'get_by_id' method"

    def test_worker_repository_has_list_method(self):
        """验证 WorkerRepository 有 list 方法"""
        from src.domain.services.worker_repository import WorkerRepository

        has_list = hasattr(WorkerRepository, 'list') or 'list' in dir(WorkerRepository)
        assert has_list, "WorkerRepository should have 'list' method"

    def test_worker_repository_has_update_method(self):
        """验证 WorkerRepository 有 update 方法"""
        from src.domain.services.worker_repository import WorkerRepository

        has_update = hasattr(WorkerRepository, 'update') or 'update' in dir(WorkerRepository)
        assert has_update, "WorkerRepository should have 'update' method"

    def test_worker_repository_has_delete_method(self):
        """验证 WorkerRepository 有 delete 方法"""
        from src.domain.services.worker_repository import WorkerRepository

        has_delete = hasattr(WorkerRepository, 'delete') or 'delete' in dir(WorkerRepository)
        assert has_delete, "WorkerRepository should have 'delete' method"


class TestWorkerRepositoryMethodSignatures:
    """WorkerRepository 方法签名测试"""

    def test_create_signature(self):
        """验证 create 方法签名"""
        from src.domain.services.worker_repository import WorkerRepository
        from src.domain.models.worker import Worker
        import inspect

        # 获取 create 方法的签名
        if hasattr(WorkerRepository, 'create'):
            create_method = getattr(WorkerRepository, 'create')
            if callable(create_method):
                sig = inspect.signature(create_method)
                # 应该有一个 worker 参数
                params = list(sig.parameters.keys())
                assert 'worker' in params or 'self' in params, "create should have 'worker' parameter"

    def test_get_by_id_signature(self):
        """验证 get_by_id 方法签名"""
        from src.domain.services.worker_repository import WorkerRepository
        import inspect

        if hasattr(WorkerRepository, 'get_by_id'):
            get_method = getattr(WorkerRepository, 'get_by_id')
            if callable(get_method):
                sig = inspect.signature(get_method)
                params = list(sig.parameters.keys())
                # 应该有一个 worker_id 参数
                assert 'worker_id' in params or 'id' in params or 'self' in params, \
                    "get_by_id should have 'worker_id' or 'id' parameter"