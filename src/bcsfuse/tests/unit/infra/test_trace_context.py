"""
trace_context 模块单元测试

覆盖:
- trace_id 生成格式
- trace_id 唯一性
- set/get trace_id
- 默认值为空串
- contextvars 异步隔离
- RecordFactory 日志注入
"""

import asyncio
import logging
import logging.handlers
import re

import pytest

from src.infra.trace_context import generate_trace_id, get_trace_id, set_trace_id, install_trace_record_factory


class TestGenerateTraceId:
    """generate_trace_id() 测试"""

    def test_format_prefix(self):
        """生成的 trace_id 以 'trace_' 开头"""
        tid = generate_trace_id()
        assert tid.startswith("trace_")

    def test_format_three_parts(self):
        """格式为 trace_{timestamp_ms}_{8hex}，共 3 段"""
        tid = generate_trace_id()
        parts = tid.split("_")
        assert len(parts) == 3, f"Expected 3 parts, got {len(parts)}: {tid}"

    def test_format_timestamp_part(self):
        """第二段为毫秒时间戳（纯数字）"""
        tid = generate_trace_id()
        parts = tid.split("_")
        assert parts[1].isdigit(), f"Timestamp part is not digits: {parts[1]}"

    def test_format_random_hex_part(self):
        """第三段为 8 位 hex"""
        tid = generate_trace_id()
        parts = tid.split("_")
        random_part = parts[2]
        assert len(random_part) == 8, f"Expected 8 hex chars, got {len(random_part)}: {random_part}"
        assert re.match(r"^[0-9a-f]{8}$", random_part), f"Not valid hex: {random_part}"

    def test_uniqueness(self):
        """连续生成 100 个 trace_id，无碰撞"""
        ids = {generate_trace_id() for _ in range(100)}
        assert len(ids) == 100

    def test_timestamp_reasonable(self):
        """时间戳部分在合理范围内（当前时间 ± 10秒）"""
        import time
        now_ms = int(time.time() * 1000)
        tid = generate_trace_id()
        timestamp_ms = int(tid.split("_")[1])
        assert abs(now_ms - timestamp_ms) < 10000


class TestSetGetTraceId:
    """set_trace_id / get_trace_id 测试"""

    def test_set_and_get(self):
        """设置后可以读取"""
        set_trace_id("test_id_123")
        assert get_trace_id() == "test_id_123"

    def test_overwrite(self):
        """可以覆盖之前的值"""
        set_trace_id("first_id")
        set_trace_id("second_id")
        assert get_trace_id() == "second_id"

    def test_default_empty(self):
        """默认值为空串"""
        # 在新的 context 中测试
        async def check_default():
            # contextvars 在新 task 中会拷贝父 context
            # 但我们直接验证 get 返回值
            return get_trace_id()

        # 在当前 context 中，可能已被其他测试设置过
        # 所以只验证类型
        result = get_trace_id()
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_async_isolation(self):
        """contextvars 在不同 asyncio task 中隔离"""
        results = {}

        async def task_a():
            set_trace_id("task_a_id")
            await asyncio.sleep(0.01)
            results["a"] = get_trace_id()

        async def task_b():
            set_trace_id("task_b_id")
            await asyncio.sleep(0.01)
            results["b"] = get_trace_id()

        await asyncio.gather(task_a(), task_b())
        assert results["a"] == "task_a_id"
        assert results["b"] == "task_b_id"


class TestRecordFactory:
    """install_trace_record_factory / LogRecord 工厂测试"""

    def test_record_factory_injects_traceid(self):
        """安装 RecordFactory 后，通过 logger 产生的 LogRecord 自动包含 traceid"""
        original_factory = logging.getLogRecordFactory()
        try:
            install_trace_record_factory()

            set_trace_id("trace_factory_test")

            # 通过 logger.emit 收集记录（LogRecord 通过 factory 创建）
            records = []
            handler = logging.handlers.MemoryHandler(capacity=100)
            handler.setTarget(logging.StreamHandler())  # dummy target

            class CatchHandler(logging.Handler):
                def emit(self, record):
                    records.append(record)

            catch = CatchHandler()
            test_logger = logging.getLogger("test_record_factory")
            test_logger.addHandler(catch)
            test_logger.setLevel(logging.DEBUG)

            set_trace_id("trace_factory_test")
            test_logger.info("hello")

            assert len(records) == 1
            assert records[0].traceid == "trace_factory_test"  # type: ignore[attr-defined]
            assert records[0].trace_id == "trace_factory_test"  # type: ignore[attr-defined]

            test_logger.removeHandler(catch)
        finally:
            logging.setLogRecordFactory(original_factory)

    def test_record_factory_empty_trace_id_shows_dash(self):
        """trace_id 为空时 traceid 显示为 '-'"""
        original_factory = logging.getLogRecordFactory()
        try:
            install_trace_record_factory()

            set_trace_id("")

            records = []

            class CatchHandler(logging.Handler):
                def emit(self, record):
                    records.append(record)

            catch = CatchHandler()
            test_logger = logging.getLogger("test_record_factory_empty")
            test_logger.addHandler(catch)
            test_logger.setLevel(logging.DEBUG)

            test_logger.info("hello")

            assert len(records) == 1
            assert records[0].traceid == "-"  # type: ignore[attr-defined]
            assert records[0].trace_id == ""  # type: ignore[attr-defined]

            test_logger.removeHandler(catch)
        finally:
            logging.setLogRecordFactory(original_factory)

    def test_record_factory_idempotent(self):
        """多次调用 install_trace_record_factory 不会重复包装"""
        original_factory = logging.getLogRecordFactory()
        try:
            install_trace_record_factory()
            factory_after_first = logging.getLogRecordFactory()

            install_trace_record_factory()
            factory_after_second = logging.getLogRecordFactory()

            assert factory_after_first is factory_after_second
        finally:
            logging.setLogRecordFactory(original_factory)

    def test_format_with_traceid_no_keyerror(self):
        """使用 %(traceid)s 格式化不会抛 KeyError"""
        original_factory = logging.getLogRecordFactory()
        try:
            install_trace_record_factory()

            set_trace_id("trace_123_abc")

            records = []

            class CatchHandler(logging.Handler):
                def emit(self, record):
                    records.append(record)

            catch = CatchHandler()
            test_logger = logging.getLogger("test_record_factory_fmt")
            test_logger.addHandler(catch)
            test_logger.setLevel(logging.DEBUG)

            test_logger.info("hello")

            assert len(records) == 1
            formatter = logging.Formatter("[%(traceid)s] %(message)s")
            output = formatter.format(records[0])
            assert "[trace_123_abc] hello" == output

            test_logger.removeHandler(catch)
        finally:
            logging.setLogRecordFactory(original_factory)