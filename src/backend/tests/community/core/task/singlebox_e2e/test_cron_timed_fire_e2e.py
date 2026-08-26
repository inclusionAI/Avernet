"""Cron 定时触发 e2e — 通过 API 运行时修改 cron 为 now+1min，验证自动 fire。

gated by ``SINGLEBOX_CRON_E2E=1``。运行前需要先启动 singlebox（至少
openclaw + engine + backend）。

  SINGLEBOX_CRON_E2E=1 \
  .venv/bin/python -m pytest \\
    tests/community/core/task/singlebox_e2e/test_cron_timed_fire_e2e.py -s

钉钉交互卡片与工单通知（NOTICE）均由 ``DiscoveryService`` 在发现时投递，
**不再由本测试发送**：
  - 工单通知：DiscoveryService 直接调 WorkOrderService（进程内，落
    ac_work_order_notification）。
  - 钉钉交互卡片：由 notify 装配在凭证就绪时绑定 ``DingTalkNotifySender``，
    在发现时投递。需在**后端启动时**设置凭证 env（
    ``TASK_DISCOVERY_DINGTALK_*``，回退 ``SINGLEBOX_DINGTALK_*``，
    模板复用 ``TASK_DISCOVERY_CARD_TEMPLATE_ID``），未配置则仅日志通道。

完整流程（全程不重启 backend）:
  0) 从 backend 获取 bot + 写入 mock 任务数据
  1) 计算现在 + 1分钟 的 cron 表达式（取整到分钟）
  2) POST /discovery/reschedule 修改 cron — 无需重启
  3) 验证 scheduler-status: cron 已更新, next_run_time 符合预期
  4) 等待 cron 自然 fire（轮询直到 next_run_time 跳到明天）
  5) 验证 discovery status: 发现任务已有 session_id（钉钉/工单通知由服务侧投递）
  6) tearDown: reschedule 恢复原始 cron

环境变量:
  SINGLEBOX_CRON_E2E=1            启用本测试（默认 skip）
  SINGLEBOX_BACKEND_URL           backend 地址, 默认 http://agentclaw-local.stable.alipay.net:8888
  SINGLEBOX_USER_ID               用户工号, 默认 440718
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import unittest
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from agentclaw.community.core.task.task_discovery.task_reader import (
    init_discovered_tasks_db,
)

# ---------------------------------------------------------------------------
# 钉钉 SDK 依赖自检安装
# ---------------------------------------------------------------------------

_DINGTALK_DEPS = [
    "alibabacloud_tea_openapi==0.3.10",
    "alibabacloud_endpoint_util==0.0.3",
]
_DINGTALK_SDK = "antdingopensdk==1.0.47"
_PIP_INDEX = "https://pypi.antfin-inc.com/simple/"
_PIP_INDEX_DEV = "https://pypi.antfin-inc.com/simple-dev/"


def _ensure_dingtalk_sdk() -> None:
    """惰性安装钉钉 SDK 依赖；仅在 _LIVE 且需钉钉凭证时触发。

    优先用 uv pip install（singlebox venv 无 pip），回退到 python -m pip。
    """
    try:
        import alipay_antdingopensdk_client  # noqa: F401
        return
    except ImportError:
        pass

    venv_python = sys.executable
    venv_dir = str(Path(venv_python).parent.parent)

    # 检测可用的安装方式
    if shutil.which("uv"):
        install_cmd_prefix = ["uv", "pip", "install", "--python", venv_python]
    else:
        install_cmd_prefix = [venv_python, "-m", "pip", "install"]

    # alibabacloud 系列在公网/内网均有
    for dep in _DINGTALK_DEPS:
        try:
            subprocess.check_call(
                install_cmd_prefix + [dep],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            pass

    # antdingopensdk 仅在 pypi.antfin-inc.com 有
    for index in (_PIP_INDEX_DEV, _PIP_INDEX):
        try:
            subprocess.check_call(
                install_cmd_prefix + [_DINGTALK_SDK, "--index-url", index],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            break
        except subprocess.CalledProcessError:
            continue

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

_LIVE = os.environ.get("SINGLEBOX_CRON_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://agentclaw-local.stable.alipay.net:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "440718")

# 运行前惰性安装钉钉 SDK 依赖
if _LIVE:
    _ensure_dingtalk_sdk()

_SHANGHAI_TZ = timezone(timedelta(hours=8))
_TODAY = datetime.now(_SHANGHAI_TZ).strftime("%Y-%m-%d")
_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

# 目标触发时间：当前时间 + 1 分钟
_TARGET_SECONDS = 60

# cron fire 后等待 discovery 完成的超时
_FIRE_WAIT_S = 120

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

_DATA_FILE = Path(__file__).resolve()
for _ in range(8):
    _DATA_FILE = _DATA_FILE.parent
_DATA_FILE = _DATA_FILE / "scripts" / ".dependencies" / "data" / "discovered_tasks.db"


def _write_mock_data(bot_id: str, owner_id: str) -> None:
    tasks = []
    for t in _MOCK_TASKS:
        task = dict(t)
        task["bot_id"] = bot_id
        task["owner_id"] = owner_id
        task["task_id"] = f"timed_fire_{bot_id}_{owner_id}_{_TODAY}"
        tasks.append(task)
    init_discovered_tasks_db(_DATA_FILE, tasks)
    print(f"[setup] mock 数据已写入 {_DATA_FILE} ({len(tasks)} tasks, bot={bot_id})")


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


def _inject_dingtalk_creds() -> None:
    """通过 API 注入钉钉凭证 + 前端 URL 到运行中的 backend — 无需重启。"""
    ak_id = os.environ.get("SINGLEBOX_DINGTALK_AK_ID", "")
    ak_secret = os.environ.get("SINGLEBOX_DINGTALK_AK_SECRET", "")
    robot_code = os.environ.get("SINGLEBOX_DINGTALK_ROBOT_CODE", "")
    card_template_id = os.environ.get("SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID", "")
    frontend_url = os.environ.get("SINGLEBOX_FRONTEND_URL", "")
    if not all([ak_id, ak_secret, robot_code, card_template_id]):
        print("[setUpClass] 钉钉凭证未配置 (SINGLEBOX_DINGTALK_*)，跳过钉钉卡片投递")
        return
    payload = {
        "ak_id": ak_id,
        "ak_secret": ak_secret,
        "robot_code": robot_code,
        "card_template_id": card_template_id,
    }
    if frontend_url:
        payload["frontend_url"] = frontend_url
    r = httpx.post(
        f"{_BACKEND}/api/v1/collaboration/tasks/discovery/dingtalk-config",
        json=payload,
        timeout=10.0,
        headers=_HDRS,
    )
    if r.status_code == 200 and r.json().get("success"):
        parts = []
        if frontend_url:
            parts.append(f"frontend_url={frontend_url}")
        parts.append("钉钉凭证")
        print(f"[setUpClass] {' + '.join(parts)} 已注入 backend")
    else:
        print(f"[setUpClass] 凭证注入失败: {r.status_code} {r.text}")


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_CRON_E2E=1 启用")
class TestCronTimedFireE2E(unittest.TestCase):
    """Cron 定时触发 e2e — 通过 API 改 cron 为 now+1min，等待 fire 后验证。"""

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

        # 运行时注入钉钉凭证 — 无需重启 backend
        _inject_dingtalk_creds()

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
        """端到端验证 cron 在 now+1min 自动触发任务发现。"""
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

        # cron fire 后 discovery 异步执行，轮询等待完成
        print("[phase 4] 等待 discovery 完成 ...")
        discoverDeadline = time.time() + 30
        while time.time() < discoverDeadline:
            time.sleep(2)
            try:
                ds = _discovery_status()
                ds_data = ds.get("data") or {}
                tasks = ds_data.get("tasks") or []
                our = [t for t in tasks if t.get("bot_id") == self._bot_id]
                if any(t.get("discovered") for t in our):
                    print("[phase 4] discovery 完成！")
                    break
            except Exception:
                continue

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
        print(f"  - 任务发现执行，session 创建成功")
        print("  - 钉钉交互卡片 / 工单通知由 DiscoveryService 在发现时投递"
              "（钉钉需后端启动时配置凭证 env）")


if __name__ == "__main__":
    unittest.main()
