"""
trace_context 测试的本地 conftest — 跳过全局重置 fixture

全局 conftest 的 reset_stores_before_test 会导入 app 依赖，
对纯 unit test 无需这些重置逻辑，在此覆盖为空实现。
"""

import pytest


@pytest.fixture(scope="function", autouse=True)
def reset_stores_before_test():
    """覆盖全局 conftest 的重置 fixture，避免导入 app 依赖"""
    yield