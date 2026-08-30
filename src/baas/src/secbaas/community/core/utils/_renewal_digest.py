"""Renewal result digest logging — the single surviving home of ttl_renew_digest.

monitor/采集按 logger 名 ``arca-renew-digest`` 过滤并解析 CSV 行进行
续期成功率/失败率告警。该 logger 名与 CSV 字段顺序是监控采集契约，
必须保持原样不变。

历史：digest 三件套（logger、``_log_renew_digest``、``_ttl_for_digest``）原本
定义在 ``core/service/health_check/sandbox/_sandbox_device_router.py``。
为让新版 ``DeadlineRenewalScheduler`` 复用同一条告警链路，三件套被抽离到
本中立模块，router 改为 re-export 兼容层。Phase 3 删除 legacy router 类后，
本模块是 digest 的唯一存活点 — 请勿将 digest 逻辑迁回或复制到别处。
"""

from __future__ import annotations

from secbaas.community.logger import get_logger

# 专用 digest 日志器：独立命名便于 monitor/采集按 logger 名过滤
RENEWAL_DIGEST_LOGGER = get_logger("arca-renew-digest")

# 仅用于 digest 日志失败时的防御性 warning（不进入 arca-renew-digest 采集流）
_MODULE_LOGGER = get_logger(__name__)


def ttl_for_digest(ttl: str | None) -> str:
    """把 TTL 时间规整为 monitor 友好格式：去空格、缺省置 ``-``。"""
    if not ttl:
        return "-"
    return ttl.replace(" ", "-")


def log_renew_digest(
    *,
    run_uuid: str,
    table_id: int,
    table_type: str,
    arca_device_id: str,
    result: str,
    ttl_before: str | None,
    ttl_after: str | None,
) -> None:
    """续期结果 digest（monitor 用，逗号分隔字段，便于采集/告警）。

    首字段为 run_uuid 用于区分不同轮次/请求；不含 error 详情（可能很长/带换行，
    会破坏 monitor 格式），错误详情由各分支已有的 warning 日志记录。
    best-effort，日志失败不影响续期流程。

    ttl 时间去掉空格（``2026-05-27 21:15:05`` -> ``2026-05-27-21:15:05``），
    缺失/空值置为 ``-``，避免破坏逗号分隔格式。
    """
    try:
        before = ttl_for_digest(ttl_before)
        after = ttl_for_digest(ttl_after)
        RENEWAL_DIGEST_LOGGER.info(
            "ttl_renew_digest,%s,%s,%s,%s,%s,%s,%s,%s",
            run_uuid,
            "renew",
            table_id,
            table_type,
            arca_device_id,
            result,
            before,
            after,
        )
    except Exception as e:  # pragma: no cover - defensive
        _MODULE_LOGGER.warning("[ttl_renew_digest] log failed (non-fatal): %s", e)
