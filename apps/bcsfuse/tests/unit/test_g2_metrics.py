"""
G2 Metrics 单元测试

测试 G2冲突分析监控指标的记录和统计功能。
"""

import pytest
import time

from src.infra.observability.g2_metrics import (
    G2Metrics,
    G2LayerStats,
    get_g2_metrics,
    reset_g2_metrics,
)


class TestG2LayerStats:
    """G2LayerStats 测试"""

    def test_initial_state(self):
        """测试初始状态"""
        stats = G2LayerStats()
        assert stats.total_calls == 0
        assert stats.success_count == 0
        assert stats.failed_count == 0
        assert stats.total_latency_ms == 0
        assert stats.max_latency_ms == 0

    def test_record_success(self):
        """测试记录成功调用"""
        stats = G2LayerStats()
        stats.record(success=True, latency_ms=100)

        assert stats.total_calls == 1
        assert stats.success_count == 1
        assert stats.failed_count == 0
        assert stats.total_latency_ms == 100

    def test_record_failure(self):
        """测试记录失败调用"""
        stats = G2LayerStats()
        stats.record(success=False, latency_ms=50)

        assert stats.total_calls == 1
        assert stats.success_count == 0
        assert stats.failed_count == 1

    def test_multiple_records(self):
        """测试多次记录"""
        stats = G2LayerStats()
        stats.record(success=True, latency_ms=100)
        stats.record(success=True, latency_ms=200)
        stats.record(success=False, latency_ms=150)

        assert stats.total_calls == 3
        assert stats.success_count == 2
        assert stats.failed_count == 1
        assert stats.total_latency_ms == 450
        assert stats.max_latency_ms == 200

    def test_avg_latency(self):
        """测试平均延迟计算"""
        stats = G2LayerStats()
        stats.record(success=True, latency_ms=100)
        stats.record(success=True, latency_ms=200)

        assert stats.avg_latency_ms == 150.0

    def test_avg_latency_no_calls(self):
        """测试无调用时的平均延迟"""
        stats = G2LayerStats()
        assert stats.avg_latency_ms == 0.0

    def test_success_rate(self):
        """测试成功率计算"""
        stats = G2LayerStats()
        stats.record(success=True, latency_ms=100)
        stats.record(success=True, latency_ms=100)
        stats.record(success=False, latency_ms=100)

        assert stats.success_rate == pytest.approx(2/3)


class TestG2Metrics:
    """G2Metrics 测试"""

    @pytest.fixture
    def metrics(self):
        """创建新的 metrics 实例"""
        return G2Metrics()

    def test_initial_state(self, metrics):
        """测试初始状态"""
        assert metrics.total_analyses == 0
        assert "llm" in metrics.layer_stats
        assert "v2" in metrics.layer_stats
        assert "legacy" in metrics.layer_stats

    def test_record_analysis_success(self, metrics):
        """测试记录成功分析"""
        metrics.record_analysis("llm", "success", 1500)

        assert metrics.layer_stats["llm"].total_calls == 1
        assert metrics.layer_stats["llm"].success_count == 1

    def test_record_analysis_failed(self, metrics):
        """测试记录失败分析"""
        metrics.record_analysis("v2", "failed", 200)

        assert metrics.layer_stats["v2"].total_calls == 1
        assert metrics.layer_stats["v2"].failed_count == 1

    def test_record_fallback(self, metrics):
        """测试记录Fallback"""
        metrics.record_fallback("llm", "v2", "timeout")

        assert metrics.fallback_counts["llm_to_v2"] == 1

    def test_record_severity(self, metrics):
        """测试记录严重程度"""
        metrics.record_severity("high")
        metrics.record_severity("medium")
        metrics.record_severity("high")

        assert metrics.severity_counts["high"] == 2
        assert metrics.severity_counts["medium"] == 1

    def test_record_final_result(self, metrics):
        """测试记录最终结果"""
        metrics.record_final_result("llm", "medium")

        assert metrics.total_analyses == 1
        assert metrics.final_source_counts["llm"] == 1
        assert metrics.severity_counts["medium"] == 1

    def test_track_analysis_context_manager_success(self, metrics):
        """测试上下文管理器追踪成功分析"""
        with metrics.track_analysis("llm") as tracker:
            time.sleep(0.01)  # 模拟分析耗时
            tracker["success"] = True

        assert metrics.layer_stats["llm"].total_calls == 1
        assert metrics.layer_stats["llm"].success_count == 1

    def test_track_analysis_context_manager_failure(self, metrics):
        """测试上下文管理器追踪失败分析"""
        with metrics.track_analysis("v2") as tracker:
            time.sleep(0.01)
            tracker["success"] = False

        assert metrics.layer_stats["v2"].total_calls == 1
        assert metrics.layer_stats["v2"].failed_count == 1

    def test_get_summary(self, metrics):
        """测试获取摘要"""
        metrics.record_analysis("llm", "success", 1500)
        metrics.record_fallback("llm", "v2", "test")
        metrics.record_final_result("v2", "medium")

        summary = metrics.get_summary()

        assert summary["total_analyses"] == 1
        assert "layer_stats" in summary
        assert summary["layer_stats"]["llm"]["total_calls"] == 1
        assert summary["fallback_counts"]["llm_to_v2"] == 1

    def test_reset(self, metrics):
        """测试重置"""
        metrics.record_analysis("llm", "success", 1000)
        metrics.record_final_result("llm", "high")

        metrics.reset()

        assert metrics.total_analyses == 0
        assert metrics.layer_stats["llm"].total_calls == 0
        assert metrics.severity_counts["high"] == 0


class TestGlobalMetrics:
    """全局 metrics 实例测试"""

    def test_get_g2_metrics_singleton(self):
        """测试获取全局单例"""
        reset_g2_metrics()
        metrics1 = get_g2_metrics()
        metrics2 = get_g2_metrics()

        assert metrics1 is metrics2

    def test_reset_g2_metrics(self):
        """测试重置全局单例"""
        metrics = get_g2_metrics()
        metrics.record_analysis("llm", "success", 500)

        reset_g2_metrics()

        assert metrics.total_analyses == 0


class TestMetricsIntegration:
    """指标集成测试"""

    def test_full_fallback_flow(self):
        """测试完整Fallback流程的指标记录"""
        metrics = G2Metrics()

        # Layer 1 尝试并失败
        metrics.record_analysis("llm", "failed", 1500)
        metrics.record_fallback("llm", "v2", "timeout")

        # Layer 2 尝试并失败
        metrics.record_analysis("v2", "failed", 200)
        metrics.record_fallback("v2", "legacy", "none_result")

        # Layer 3 成功
        metrics.record_analysis("legacy", "success", 50)
        metrics.record_final_result("legacy", "medium")

        summary = metrics.get_summary()

        # 验证各层调用情况
        assert summary["layer_stats"]["llm"]["total_calls"] == 1
        assert summary["layer_stats"]["llm"]["success_rate"] == "0.0%"
        assert summary["layer_stats"]["v2"]["total_calls"] == 1
        assert summary["layer_stats"]["legacy"]["total_calls"] == 1
        assert summary["layer_stats"]["legacy"]["success_rate"] == "100.0%"

        # 验证Fallback路径
        assert summary["fallback_counts"]["llm_to_v2"] == 1
        assert summary["fallback_counts"]["v2_to_legacy"] == 1

        # 验证最终结果
        assert summary["total_analyses"] == 1
        assert summary["final_source_distribution"]["legacy"] == 1
        assert summary["severity_distribution"]["medium"] == 1

    def test_layer1_success_flow(self):
        """测试Layer 1直接成功的流程"""
        metrics = G2Metrics()

        # Layer 1 成功
        metrics.record_analysis("llm", "success", 1200)
        metrics.record_final_result("llm", "high")

        summary = metrics.get_summary()

        assert summary["layer_stats"]["llm"]["success_rate"] == "100.0%"
        assert summary["total_analyses"] == 1
        assert summary["final_source_distribution"]["llm"] == 1
        # 验证没有fallback
        assert summary["fallback_counts"]["llm_to_v2"] == 0