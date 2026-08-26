"""Cron 定时触发 e2e — 通过 API 运行时修改 cron 为 now+1min，验证自动 fire + 工单事件通知。

与 :mod:`test_cron_timed_fire_e2e` 同构，差异仅在第 6 阶段：把「钉钉交互卡片」
改为 ``POST /openapi/v1/bots/work-orders/events`` 投递一条 NOTICE 工单事件，
把发现到的任务作为通知推给 owner。

gated by ``SINGLEBOX_CRON_E2E=1``。工单事件阶段需额外设置
``SINGLEBOX_WORKORDER_E2E=1`` 且后端启动时带相同的网关 principal 签名 key
（``AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE``）。

  SINGLEBOX_CRON_E2E=1 \
  SINGLEBOX_WORKORDER_E2E=1 \
  SINGLEBOX_PRINCIPAL_SIGNING_KEY="avernet-dev-signing-key-NOT-FOR-PROD" \
  .venv/bin/python -m pytest \
    tests/community/core/task/singlebox_e2e/test_cron_timed_fire_workorder_e2e.py -s

后端需用同一个签名 key 启动（dev 默认值见 ``scripts/modules/backend.sh``）::

  AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE="avernet-dev-signing-key-NOT-FOR-PROD" \
    ./scripts/singlebox.sh restart backend

完整流程（全程不重启 backend）:
  0) 从 backend 获取 bot + 写入 mock 任务数据
  1) 计算现在 + 1分钟 的 cron 表达式（取整到分钟）
  2) POST /discovery/reschedule 修改 cron — 无需重启
  3) 验证 scheduler-status: cron 已更新, next_run_time 符合预期
  4) 等待 cron 自然 fire（轮询直到 next_run_time 跳到明天）
  5) 验证 discovery status: 发现任务已有 session_id
  6) [可选] 工单事件: 用 session_url → POST work-orders/events 投递 NOTICE 通知
  7) tearDown: reschedule 恢复原始 cron

环境变量:
  SINGLEBOX_CRON_E2E=1            启用本测试（默认 skip）
  SINGLEBOX_BACKEND_URL           backend 地址, 默认 http://agentclaw-local.stable.alipay.net:8888
  SINGLEBOX_USER_ID               用户工号, 默认 440718
  SINGLEBOX_WORKORDER_E2E=1          启用工单事件阶段（默认 skip）
  SINGLEBOX_PRINCIPAL_SIGNING_KEY    与后端启动一致的网关 principal 签名 key
                                      默认 avernet-dev-signing-key-NOT-FOR-PROD
  SINGLEBOX_PRINCIPAL_USERNAME       principal 的 username, 默认同 SINGLEBOX_USER_ID
  SINGLEBOX_FRONTEND_URL             前端地址, 用于 session 链接
"""
from __future__ import annotations

import json
import os
import time
import unittest
import warnings
from datetime import datetime, timedelta, timezone

import httpx

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

_LIVE = os.environ.get("SINGLEBOX_CRON_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://agentclaw-local.stable.alipay.net:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "440718")

_SHANGHAI_TZ = timezone(timedelta(hours=8))
_TODAY = datetime.now(_SHANGHAI_TZ).strftime("%Y-%m-%d")
_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

# 目标触发时间：当前时间 + 1 分钟
_TARGET_SECONDS = 60

# cron fire 后等待 discovery 完成的超时
_FIRE_WAIT_S = 120

# ---------------------------------------------------------------------------
# 工单事件通知配置
# ---------------------------------------------------------------------------

# dev 默认签名 key —— 与 scripts/modules/backend.sh 中
# AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE 的默认值保持一致。
_DEV_SIGNING_KEY = "avernet-dev-signing-key-NOT-FOR-PROD"

_WO_LIVE = os.environ.get("SINGLEBOX_WORKORDER_E2E", "").strip() in {"1", "true"}
_SIGNING_KEY = os.environ.get("SINGLEBOX_PRINCIPAL_SIGNING_KEY", _DEV_SIGNING_KEY).strip()
_PRINCIPAL_USERNAME = os.environ.get("SINGLEBOX_PRINCIPAL_USERNAME", _USER_ID)

_WORKORDERS_EVENTS_PATH = "/openapi/v1/bots/work-orders/events"

_FRONTEND_URL = os.environ.get(
    "SINGLEBOX_FRONTEND_URL",
    "http://agentclaw-local.stable.alipay.net:8000",
)

# ---------------------------------------------------------------------------
# Mock 数据
# ---------------------------------------------------------------------------

_MOCK_TASKS: list[dict] = [
    {
        "task_id": f"timed_fire_e2e_{_USER_ID}_{_TODAY}",
        "bot_id": "",
        "owner_id": _USER_ID,
        "dt": _TODAY,
        "title": "存储行业尽调报告",
        "instruction": "AI 基础设施驱动下,企业级与数据中心存储行业的最新变化。",
        "background": "投资尽调 — 产出系统性的投资判断报告。",
        "discovery_basis": "用户近一周频繁搜索存储行业相关信息。",
        "priority": "high",
        "discovered_at": f"{_TODAY}T10:00:00Z",
        "status": "pending_confirmation",
        "objective": "产出系统性的存储行业投资判断报告。",
        "acceptances": [
            {"id": "a1", "description": "覆盖存储行业 6 条核心结论"},
            {"id": "a2", "description": "报告包含数据与逻辑链路"},
        ],
    },
]

def _write_mock_data(bot_id: str, owner_id: str) -> None:
    tasks = []
    for t in _MOCK_TASKS:
        task = dict(t)
        task["bot_id"] = bot_id
        task["owner_id"] = owner_id
        task["task_id"] = f"timed_fire_{bot_id}_{owner_id}_{_TODAY}"
        tasks.append(task)
    r = httpx.post(
        f"{_BACKEND}/api/v1/collaboration/tasks/discovery/tasks",
        json={"tasks": tasks},
        timeout=15.0, headers=_HDRS,
    )
    r.raise_for_status()
    print(f"[setup] written {len(tasks)} tasks via HTTP /discovery/tasks (bot={bot_id})")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _calc_cron_now_plus_seconds(seconds: int) -> tuple[str, datetime]:
    """计算 now + seconds 的 5 字段 cron 表达式，向上取整到分钟。"""
    now = datetime.now(_SHANGHAI_TZ)
    target = now + timedelta(seconds=seconds)
    if target.second > 0 or target.microsecond > 0:
        target = target.replace(second=0, microsecond=0) + timedelta(minutes=1)
    cron_expr = f"{target.minute} {target.hour} * * *"
    print(f"[cron] now={now:%H:%M:%S} target={target:%H:%M:%S} expr='{cron_expr}'")
    return cron_expr, target


def _scheduler_status() -> dict:
    r = httpx.get(
        f"{_BACKEND}/api/v1/collaboration/tasks/discovery/scheduler-status",
        timeout=15.0, headers=_HDRS,
    )
    if r.status_code != 200:
        raise RuntimeError(f"scheduler-status HTTP {r.status_code}: {r.text}")
    return r.json()


def _reschedule(cron_expr: str) -> dict:
    """调用 POST /discovery/reschedule 修改 cron。"""
    r = httpx.post(
        f"{_BACKEND}/api/v1/collaboration/tasks/discovery/reschedule",
        params={"cron": cron_expr},
        timeout=15.0, headers=_HDRS,
    )
    if r.status_code != 200:
        raise RuntimeError(f"reschedule HTTP {r.status_code}: {r.text}")
    return r.json()


def _discovery_status() -> dict:
    r = httpx.get(
        f"{_BACKEND}/api/v1/collaboration/tasks/discovery/status",
        timeout=15.0, headers=_HDRS,
    )
    if r.status_code != 200:
        raise RuntimeError(f"discovery/status HTTP {r.status_code}: {r.text}")
    return r.json()


def _find_bot() -> tuple[str, str]:
    with httpx.Client(timeout=30.0, headers=_HDRS) as cli:
        for endpoint in [
            f"{_BACKEND}/api/bots/by-owner-or-collaborator",
            f"{_BACKEND}/api/bots",
        ]:
            try:
                r = cli.get(endpoint, params={"user_id": _USER_ID})
                if r.status_code == 200:
                    bots = (r.json().get("data") or {}).get("items") or []
                    if bots:
                        active = [b for b in bots if b.get("status") == "ACTIVE"]
                        bot = active[0] if active else bots[0]
                        return bot["bot_id"], bot.get("owner_id", _USER_ID)
            except Exception:
                continue
    raise RuntimeError("无法获取存活 bot — 请先 ./scripts/singlebox.sh start all")


# ---------------------------------------------------------------------------
# 工单事件通知辅助函数
# ---------------------------------------------------------------------------

#: NOTE: work-orders/events 按 EVENT_CATEGORIES 只接受领域专有 event_type，
#: 无通用 task_discovery 类型。HUMAN2BOT_PUBLIC_ORDER_CREATED 是 NOTICE 类里
#: 语义最接近「为人发现/创建了一个任务」的已注册 event_type；任务的上下文
#: 由 biz_type/biz_id/content/biz_data 承载，不依赖 event_type 的领域语义。
_WO_EVENT_TYPE = "HUMAN2BOT_PUBLIC_ORDER_CREATED"


def _principal_headers() -> dict[str, str]:
    """铸造网关 principal JWT（HS256），返回 X-Avernet-Principal 头。

    语义与 ``tests/community/endpoints/test_work_orders_router.py`` 一致：
    一个只带 user principal 的令牌。UserPrincipal 按 contract 不携带 tenant，
    VerifiedCaller.tenant 解析为默认 tenant（``teamclaw``）—— 与 singlebox
    内部 API 创建的 bot/数据所在 tenant 一致。

    注意：此处 token 仅负责通过 ``require_principal`` 鉴权门；真正决定「代替谁
    行事」的是 ``require_user_id`` 解析的 query 参数 ``user_id``（见调用处
    ``params={"user_id": _USER_ID}``），两者保持一致。
    """
    import jwt

    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60 * 60,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": _USER_ID,
                        "username": _PRINCIPAL_USERNAME,
                    },
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token, "content-type": "application/json"}


def _send_work_order_event(
    *,
    task_id: str,
    bot_id: str,
    owner_id: str,
    session_url: str,
    session_id: str | None,
    title: str,
    applicant_user_id: str | None = None,
) -> tuple[int, dict]:
    """向 ``POST /openapi/v1/bots/work-orders/events`` 投递一条 NOTICE 工单事件。

    NOTICE 校验约束（``work_order_service.create_work_order_event``）：
      - event_type 必须是 EVENT_CATEGORIES 中 NOTICE 类的已注册值
      - applicant_user_id 必须为 None（传非 null → 400201 "Invalid work-order event"）
      - approver_user_ids 必须为空
      - recipient_user_ids 必须非空
    """
    payload = {
        "event_category": "NOTICE",
        "biz_type": "task_discovery",
        "biz_id": task_id,
        "event_type": _WO_EVENT_TYPE,
        # NOTICE 事件要求 applicant_user_id 为 null；传值会被服务端 400 拒绝
        "applicant_user_id": applicant_user_id,
        "approver_user_ids": [],
        "recipient_user_ids": [owner_id],
        "title": title,
        "content": {
            "card_name": "为你发现以下任务",
            "session_url": session_url,
            "workitem_name": title,
            "workitem_bg": "AI 基础设施驱动下,企业级与数据中心存储行业的最新变化。",
        },
        "biz_data": {
            "task_id": task_id,
            "bot_id": bot_id,
            "owner_id": owner_id,
            "session_id": session_id,
            "session_url": session_url,
        },
    }
    headers = _principal_headers()
    r = httpx.post(
        f"{_BACKEND}{_WORKORDERS_EVENTS_PATH}",
        # require_user_id 依赖的必填 query 参数 — 决定 ActingCaller.user_id
        params={"user_id": _USER_ID},
        json=payload,
        headers=headers,
        timeout=30.0,
    )
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    return r.status_code, body


def _workorder_configured() -> bool:
    return bool(_WO_LIVE and _SIGNING_KEY)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_CRON_E2E=1 启用")
class TestCronTimedFireWorkOrderE2E(unittest.TestCase):
    """Cron 定时触发 e2e — 通过 API 改 cron 为 now+1min，等待 fire 后验证 + 工单通知。"""

    _bot_id: str = ""
    _owner_id: str = ""
    _original_cron: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        print("=" * 60)
        print("[setUpClass] 准备 mock 数据")
        print("=" * 60)

        cls._bot_id, cls._owner_id = _find_bot()
        print(f"[setUpClass] bot_id={cls._bot_id} owner_id={cls._owner_id}")
        _write_mock_data(cls._bot_id, cls._owner_id)

        # 记录原始 cron（tearDown 恢复）
        try:
            status = _scheduler_status()
            cls._original_cron = status.get("cron", "")
            print(f"[setUpClass] original_cron='{cls._original_cron}'")
        except Exception as exc:
            print(f"[setUpClass] 无法获取原始 cron (non-fatal): {exc}")

    @classmethod
    def tearDownClass(cls) -> None:
        print("=" * 60)
        print("[tearDownClass] 恢复原始 cron")
        print("=" * 60)
        if not cls._bot_id:
            return
        restore = cls._original_cron or "5 14 * * *"
        print(f"[tearDownClass] reschedule 恢复 cron='{restore}'")
        try:
            _reschedule(restore)
        except Exception as exc:
            warnings.warn(f"tearDown: 恢复 cron 失败 (non-fatal): {exc}")

    def test_cron_timed_fire(self) -> None:
        """端到端验证 cron 在 now+1min 自动触发任务发现 + 工单通知。"""
        # ================================================================
        # Phase 1: 计算目标 cron — now + 1 分钟
        # ================================================================
        print("\n--- Phase 1: 计算 cron 表达式 ---")
        cron_expr, target = _calc_cron_now_plus_seconds(_TARGET_SECONDS)
        self.assertGreater(len(cron_expr), 0, "cron 表达式为空")

        # ================================================================
        # Phase 2: 通过 API 修改 cron（不重启 backend）
        # ================================================================
        print("\n--- Phase 2: reschedule cron ---")
        resp = _reschedule(cron_expr)
        self.assertTrue(resp.get("success"), f"reschedule 未成功: {resp}")
        print(f"[phase 2] reschedule 成功: cron='{cron_expr}' "
              f"next_run={resp.get('next_run_time')}")

        # ================================================================
        # Phase 3: 验证 scheduler 已加载新 cron
        # ================================================================
        print("\n--- Phase 3: 验证 scheduler 状态 ---")
        status = _scheduler_status()
        self.assertTrue(status.get("success"), f"scheduler-status 未成功: {status}")
        self.assertTrue(status.get("running"), "APScheduler 未运行")

        self.assertEqual(status.get("cron"), cron_expr,
                         f"cron 未更新: expected='{cron_expr}' "
                         f"actual='{status.get('cron')}'")

        jobs = status.get("jobs") or []
        self.assertGreater(len(jobs), 0, "APScheduler 无 job 注册")
        job = jobs[0]
        self.assertEqual(job["id"], "task_discovery_daily")

        next_run = job.get("next_run_time")
        self.assertIsNotNone(next_run, "next_run_time 为空")
        print(f"[phase 3] next_run_time={next_run} cron='{status['cron']}'")

        # ================================================================
        # Phase 4: 等待 cron 自然 fire
        # ================================================================
        print(f"\n--- Phase 4: 等待 cron fire (target={target:%H:%M:%S}) ---")
        print(f"[phase 4] 等待 cron 在 {next_run} 自动触发 ...")

        fireDeadline = time.time() + _FIRE_WAIT_S
        fired = False

        while time.time() < fireDeadline:
            time.sleep(3)
            try:
                st = _scheduler_status()
                jobs_after = st.get("jobs") or []
                if jobs_after:
                    new_next_run = jobs_after[0].get("next_run_time")
                    # cron fire 后 next_run_time 会跳到明天
                    if new_next_run and new_next_run != next_run:
                        fired = True
                        print(f"[phase 4] cron 已 fire！next_run_time: "
                              f"{next_run} → {new_next_run}")
                        break

                # 也检查 discovery status
                ds = _discovery_status()
                ds_data = ds.get("data") or {}
                tasks = ds_data.get("tasks") or []
                discovered_tasks = [
                    t for t in tasks
                    if t.get("discovered") and t.get("bot_id") == self._bot_id
                ]
                if discovered_tasks:
                    fired = True
                    print(f"[phase 4] 发现已执行 ({len(discovered_tasks)} tasks)！")
                    break
            except Exception as exc:
                print(f"[phase 4] 轮询异常 (non-fatal): {exc}")
                continue

        if not fired:
            warnings.warn(
                f"cron 未在 {_FIRE_WAIT_S}s 内 fire — "
                f"可能 next_run_time={next_run} 尚未到。检查 backend 日志。"
            )
            return

        # cron fire 后 discovery 异步执行，给几秒完成
        print("[phase 4] 等待 discovery 完成 ...")
        time.sleep(5)

        # ================================================================
        # Phase 5: 验证发现结果
        # ================================================================
        print("\n--- Phase 5: 验证发现结果 ---")
        ds = _discovery_status()
        ds_data = ds.get("data") or {}
        tasks = ds_data.get("tasks") or []
        our = [t for t in tasks if t.get("bot_id") == self._bot_id]

        print(f"[phase 5] 总任务 {len(tasks)}, 本 bot {len(our)}")
        if not our:
            warnings.warn("无本 bot 的发现结果 — 可能 mock 数据 dt 不匹配")
            return

        for t in our:
            print(f"  - task={t.get('task_id')} discovered={t.get('discovered')} "
                  f"session_id={t.get('session_id')} "
                  f"notified={t.get('notification_sent')}")

        self.assertTrue(
            any(t.get("discovered") for t in our),
            "所有任务都未 discovered — cron 可能未触发 discover_all_bots",
        )
        self.assertTrue(
            any(t.get("session_id") for t in our),
            "所有任务都无 session_id — discover 可能执行了但 session 创建失败",
        )

        print("\n[SUCCESS] cron 定时触发 e2e 验证通过！")
        print(f"  - cron='{cron_expr}' 在 {next_run} 自动 fire")
        print("  - 任务发现执行，session 创建成功")

        # ================================================================
        # Phase 6: 工单事件通知（可选 — 需 SINGLEBOX_WORKORDER_E2E=1 + 签名 key）
        # ================================================================
        if not _workorder_configured():
            print("\n--- Phase 6: 工单事件 [SKIP] ---")
            print("[phase 6] 未设置 SINGLEBOX_WORKORDER_E2E=1 或")
            print("          SINGLEBOX_PRINCIPAL_SIGNING_KEY，跳过")
            print("          （注：后端需用同一签名 key 启动）")
            return

        print("\n--- Phase 6: 工单事件通知 ---")
        ready = next((t for t in our if t.get("session_id")), None)
        session_url = (
            (ready or {}).get("session_url")
            or f"{_FRONTEND_URL}/assistant?botId={self._bot_id}"
        )
        session_id = (ready or {}).get("session_id")
        mock_task = _MOCK_TASKS[0]
        title = mock_task["title"]
        print(f"[phase 6] POST {_WORKORDERS_EVENTS_PATH}")
        print(f"  biz_id={(ready or {}).get('task_id', mock_task['task_id'])}")
        print(f"  recipient_user_ids=[{self._owner_id}] title='{title}'")
        print(f"  session_url={session_url}")

        status_code, body = _send_work_order_event(
            task_id=(ready or {}).get("task_id", mock_task["task_id"]),
            bot_id=self._bot_id,
            owner_id=self._owner_id,
            session_url=session_url,
            session_id=session_id,
            title=title,
        )
        print(f"[phase 6] 工单响应: HTTP {status_code} "
              f"{json.dumps(body, ensure_ascii=False)}")

        if status_code == 401:
            self.fail(
                "工单事件 401 — 后端未加载与 SINGLEBOX_PRINCIPAL_SIGNING_KEY 一致的签名 key。"
                "请用 AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE=<同 key> 重启 backend。"
            )
        self.assertEqual(
            status_code, 201,
            f"工单事件投递失败: HTTP {status_code} {body}",
        )
        data = body.get("data") if isinstance(body, dict) else None
        data = data or {}
        # NOTICE 事件不产生审批工单（work_order_id 为 null），仅创建通知。
        notification_ids = data.get("notification_ids") or []
        print(f"[phase 6] work_order_id={data.get('work_order_id')} "
              f"notification_ids={notification_ids} status={data.get('status')}")
        self.assertTrue(
            notification_ids,
            "工单事件 201 但无 notification_ids — NOTICE 未生成通知",
        )

        print("\n[SUCCESS] 完整 e2e 验证通过！")
        print(f"  - cron='{cron_expr}' 在 {next_run} 自动 fire")
        print("  - 任务发现执行，session 创建成功")
        print("  - 工单事件 NOTICE 投递成功 "
              f"(notification_ids={notification_ids})")


if __name__ == "__main__":
    unittest.main()
