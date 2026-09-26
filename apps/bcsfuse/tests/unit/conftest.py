"""
单元测试的本地 conftest — 跳过全局 app 重置 fixture

全局 conftest 的 reset_stores_before_test 会导入 app 依赖链，
对纯 unit test 无需这些重置逻辑，在此覆盖为空实现。
"""

import pytest


@pytest.fixture(scope="function", autouse=True)
def reset_stores_before_test():
    """覆盖全局 conftest 的重置 fixture，避免导入 app 依赖链"""
    yield


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """覆盖全局 conftest 的环境设置，保留必要的测试环境变量"""
    import os
    os.environ["WORKER_REGISTRY_DATABASE_MODE"] = "sqlite"
    os.environ["WORKER_REGISTRY_SQLITE_DB_PATH"] = ":memory:"
    os.environ["LLM_ENABLED"] = "false"
    os.environ["EMBEDDING_BASE_URL"] = ""
    os.environ["EMBEDDING_AUTH_TOKEN"] = ""
    os.environ["EMBEDDING_MODEL"] = ""

    yield