"""
Feedback Store - 反馈数据存储

Phase F: 评估和样本数据持久化

设计原则：
- 支持 SQLite 本地存储
- 支持查询和分析
- 支持样本回放
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Optional

from src.domain.models.attribution_report import AttributionReport
from src.domain.models.evaluation_result import EvaluationResult
from src.domain.models.feedback_sample import FeedbackSample

logger = logging.getLogger(__name__)


# 表结构定义
CREATE_EVALUATION_RESULTS_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_evaluation_results (
    evaluation_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    question TEXT NOT NULL,
    profile_keys TEXT,
    strict_mode INTEGER NOT NULL DEFAULT 0,
    retrieval_metrics TEXT NOT NULL,
    decision_metrics TEXT NOT NULL,
    fallback_attribution TEXT,
    is_sample INTEGER NOT NULL DEFAULT 0,
    sample_reason TEXT,
    flags_enabled TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evaluation_timestamp
    ON bcsfuse_evaluation_results(timestamp);

CREATE INDEX IF NOT EXISTS idx_evaluation_is_sample
    ON bcsfuse_evaluation_results(is_sample);
"""

CREATE_FEEDBACK_SAMPLES_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_feedback_samples (
    sample_id TEXT PRIMARY KEY,
    sample_type TEXT NOT NULL,
    priority TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    question TEXT NOT NULL,
    profile_keys TEXT,
    strict_mode INTEGER NOT NULL DEFAULT 0,
    context TEXT NOT NULL DEFAULT '{}',
    retrieval_result TEXT,
    decision_result TEXT,
    fallback_attribution TEXT,
    is_reviewed INTEGER NOT NULL DEFAULT 0,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    improvement_action TEXT,
    improvement_status TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sample_type
    ON bcsfuse_feedback_samples(sample_type);

CREATE INDEX IF NOT EXISTS idx_sample_priority
    ON bcsfuse_feedback_samples(priority);

CREATE INDEX IF NOT EXISTS idx_sample_timestamp
    ON bcsfuse_feedback_samples(timestamp);
"""

CREATE_ATTRIBUTION_REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS bcsfuse_attribution_reports (
    attribution_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    fallback_type TEXT NOT NULL,
    level TEXT NOT NULL,
    description TEXT NOT NULL,
    fallback_chain TEXT NOT NULL,
    root_cause TEXT,
    contributing_factors TEXT NOT NULL DEFAULT '[]',
    impact TEXT NOT NULL DEFAULT '{}',
    recommendations TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    gmt_create TEXT NOT NULL,
    gmt_modify TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attribution_type
    ON bcsfuse_attribution_reports(fallback_type);

CREATE INDEX IF NOT EXISTS idx_attribution_level
    ON bcsfuse_attribution_reports(level);

CREATE INDEX IF NOT EXISTS idx_attribution_timestamp
    ON bcsfuse_attribution_reports(timestamp);
"""


class FeedbackStore:
    """
    反馈数据存储

    支持：
    - 评估结果存储
    - 样本存储
    - 归因报告存储
    - 查询和分析

    Attributes:
        db_path: 数据库路径
    """

    def __init__(self, db_path: str = "data/feedback.db"):
        """
        初始化 Feedback Store

        Args:
            db_path: 数据库路径
        """
        self._db_path = db_path
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row

        # 启用 WAL 模式
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")

        # 初始化表结构
        self._init_schema()

        logger.info("[FeedbackStore] Initialized with db_path=%s", db_path)

    def _init_schema(self) -> None:
        """初始化表结构"""
        cursor = self._conn.cursor()
        cursor.executescript(CREATE_EVALUATION_RESULTS_TABLE)
        cursor.executescript(CREATE_FEEDBACK_SAMPLES_TABLE)
        cursor.executescript(CREATE_ATTRIBUTION_REPORTS_TABLE)
        self._conn.commit()

    def save_evaluation_result(self, result: EvaluationResult) -> None:
        """保存评估结果"""
        cursor = self._conn.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT OR REPLACE INTO bcsfuse_evaluation_results
            (evaluation_id, timestamp, question, profile_keys, strict_mode,
             retrieval_metrics, decision_metrics, fallback_attribution,
             is_sample, sample_reason, flags_enabled, metadata,
             gmt_create, gmt_modify)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result.evaluation_id,
            result.timestamp.isoformat(),
            result.question,
            json.dumps(result.profile_keys) if result.profile_keys else None,
            1 if result.strict_mode else 0,
            result.retrieval_metrics.model_dump_json(),
            result.decision_metrics.model_dump_json(),
            json.dumps(result.fallback_attribution) if result.fallback_attribution else None,
            1 if result.is_sample else 0,
            result.sample_reason,
            json.dumps(result.flags_enabled),
            json.dumps(result.metadata),
            now,
            now,
        ))

        self._conn.commit()
        logger.debug("[FeedbackStore] Saved evaluation result: %s", result.evaluation_id)

    def save_feedback_sample(self, sample: FeedbackSample) -> None:
        """保存反馈样本"""
        cursor = self._conn.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT OR REPLACE INTO bcsfuse_feedback_samples
            (sample_id, sample_type, priority, timestamp, question, profile_keys,
             strict_mode, context, retrieval_result, decision_result,
             fallback_attribution, is_reviewed, reviewed_by, reviewed_at,
             review_notes, improvement_action, improvement_status, metadata,
             gmt_create, gmt_modify)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sample.sample_id,
            sample.sample_type.value,
            sample.priority.value,
            sample.timestamp.isoformat(),
            sample.question,
            json.dumps(sample.profile_keys) if sample.profile_keys else None,
            1 if sample.strict_mode else 0,
            json.dumps(sample.context),
            json.dumps(sample.retrieval_result) if sample.retrieval_result else None,
            json.dumps(sample.decision_result) if sample.decision_result else None,
            json.dumps(sample.fallback_attribution) if sample.fallback_attribution else None,
            1 if sample.is_reviewed else 0,
            sample.reviewed_by,
            sample.reviewed_at.isoformat() if sample.reviewed_at else None,
            sample.review_notes,
            sample.improvement_action,
            sample.improvement_status,
            json.dumps(sample.metadata),
            now,
            now,
        ))

        self._conn.commit()
        logger.debug("[FeedbackStore] Saved feedback sample: %s", sample.sample_id)

    def save_attribution_report(self, report: AttributionReport) -> None:
        """保存归因报告"""
        cursor = self._conn.cursor()
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("""
            INSERT OR REPLACE INTO bcsfuse_attribution_reports
            (attribution_id, timestamp, fallback_type, level, description,
             fallback_chain, root_cause, contributing_factors, impact,
             recommendations, metadata, gmt_create, gmt_modify)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report.attribution_id,
            report.timestamp.isoformat(),
            report.fallback_type.value,
            report.level.value,
            report.description,
            report.fallback_chain.model_dump_json(),
            report.root_cause,
            json.dumps(report.contributing_factors),
            json.dumps(report.impact),
            json.dumps(report.recommendations),
            json.dumps(report.metadata),
            now,
            now,
        ))

        self._conn.commit()
        logger.debug("[FeedbackStore] Saved attribution report: %s", report.attribution_id)

    def get_recent_evaluations(
        self,
        limit: int = 100,
    ) -> list[EvaluationResult]:
        """获取最近的评估结果"""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM bcsfuse_evaluation_results
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))

        results = []
        for row in cursor.fetchall():
            try:
                result = EvaluationResult(
                    evaluation_id=row["evaluation_id"],
                    timestamp=row["timestamp"],
                    question=row["question"],
                    profile_keys=json.loads(row["profile_keys"]) if row["profile_keys"] else None,
                    strict_mode=bool(row["strict_mode"]),
                    retrieval_metrics=json.loads(row["retrieval_metrics"]),
                    decision_metrics=json.loads(row["decision_metrics"]),
                    fallback_attribution=json.loads(row["fallback_attribution"]) if row["fallback_attribution"] else None,
                    is_sample=bool(row["is_sample"]),
                    sample_reason=row["sample_reason"],
                    flags_enabled=json.loads(row["flags_enabled"]),
                    metadata=json.loads(row["metadata"]),
                )
                results.append(result)
            except Exception as e:
                logger.warning("[FeedbackStore] Failed to parse evaluation result: %s", e)

        return results

    def get_samples_by_type(
        self,
        sample_type: str,
        limit: int = 100,
    ) -> list[FeedbackSample]:
        """按类型获取样本"""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT * FROM bcsfuse_feedback_samples
            WHERE sample_type = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (sample_type, limit))

        samples = []
        for row in cursor.fetchall():
            try:
                sample = FeedbackSample(
                    sample_id=row["sample_id"],
                    sample_type=row["sample_type"],
                    priority=row["priority"],
                    timestamp=row["timestamp"],
                    question=row["question"],
                    profile_keys=json.loads(row["profile_keys"]) if row["profile_keys"] else None,
                    strict_mode=bool(row["strict_mode"]),
                    context=json.loads(row["context"]),
                    retrieval_result=json.loads(row["retrieval_result"]) if row["retrieval_result"] else None,
                    decision_result=json.loads(row["decision_result"]) if row["decision_result"] else None,
                    fallback_attribution=json.loads(row["fallback_attribution"]) if row["fallback_attribution"] else None,
                    is_reviewed=bool(row["is_reviewed"]),
                    reviewed_by=row["reviewed_by"],
                    reviewed_at=row["reviewed_at"],
                    review_notes=row["review_notes"],
                    improvement_action=row["improvement_action"],
                    improvement_status=row["improvement_status"],
                    metadata=json.loads(row["metadata"]),
                )
                samples.append(sample)
            except Exception as e:
                logger.warning("[FeedbackStore] Failed to parse feedback sample: %s", e)

        return samples

    def count_samples(self) -> dict[str, int]:
        """统计样本数量"""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT sample_type, COUNT(*) as count
            FROM bcsfuse_feedback_samples
            GROUP BY sample_type
        """)

        counts = {}
        for row in cursor.fetchall():
            counts[row["sample_type"]] = row["count"]

        return counts


__all__ = ["FeedbackStore"]