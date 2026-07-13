"""
全局测试配置

配置测试使用内存数据库，避免测试间状态污染。
"""

import pytest
import os


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """
    配置测试环境使用内存数据库

    自动应用于所有测试，确保：
    1. 使用 SQLite 内存模式
    2. 配置必要的环境变量
    """
    # 设置数据库使用内存模式
    os.environ["WORKER_REGISTRY_DATABASE_MODE"] = "sqlite"
    os.environ["WORKER_REGISTRY_SQLITE_DB_PATH"] = ":memory:"

    # 禁用 LLM 和 Embedding 以避免测试时的依赖
    os.environ["LLM_ENABLED"] = "false"
    os.environ["EMBEDDING_BASE_URL"] = ""
    os.environ["EMBEDDING_AUTH_TOKEN"] = ""
    os.environ["EMBEDDING_MODEL"] = ""

    yield

    # 清理（可选，因为测试结束后进程退出）


@pytest.fixture(scope="function", autouse=True)
def reset_stores_before_test():
    """
    每个测试前重置存储实例

    确保测试间隔离，避免状态污染。
    """
    from src.interfaces.api.dependencies.worker_dependencies import reset_stores
    from src.interfaces.api.dependencies.fusion_dependencies import reset_fusion_services

    reset_stores()
    reset_fusion_services()

    yield

    # 测试结束后再次重置
    reset_stores()
    reset_fusion_services()


@pytest.fixture
def clean_client():
    """
    提供一个干净的 TestClient

    确保每个测试都有独立的数据库状态。
    """
    from fastapi.testclient import TestClient
    from src.interfaces.api.app import app
    from src.interfaces.api.dependencies.worker_dependencies import reset_stores, use_in_memory_stores

    # 强制使用内存数据库
    use_in_memory_stores()

    with TestClient(app) as client:
        yield client

    # 清理
    reset_stores()