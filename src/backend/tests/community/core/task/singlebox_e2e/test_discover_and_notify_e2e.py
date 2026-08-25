"""discover → 拿 session_url → 钉钉卡片带 session_url 投递。

用法:
  SINGLEBOX_CRON_E2E=1 \
  SINGLEBOX_DINGTALK_E2E=1 \
  SINGLEBOX_DINGTALK_AK_ID="xxx" \
  SINGLEBOX_DINGTALK_AK_SECRET="xxx" \
  SINGLEBOX_DINGTALK_ROBOT_CODE="xxx" \
  SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID="xxx.schema" \
  SINGLEBOX_USER_ID="440718" \
  .venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_discover_and_notify_e2e.py -s -v

或者直接运行:
  .venv/bin/python tests/community/core/task/singlebox_e2e/test_discover_and_notify_e2e.py \
    --bot-id=20260824_xxx --notify-user=440718
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import unittest
import warnings
from datetime import datetime
from pathlib import Path

import httpx

# ─── 配置 ─────────────────────────────────────────────────────────────────
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "440718")
_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}
_TODAY = datetime.now().strftime("%Y-%m-%d")

_DT_LIVE = os.environ.get("SINGLEBOX_DINGTALK_E2E", "").strip() in {"1", "true"}
_DT_AK_ID = os.environ.get("SINGLEBOX_DINGTALK_AK_ID", "")
_DT_AK_SECRET = os.environ.get("SINGLEBOX_DINGTALK_AK_SECRET", "")
_DT_ROBOT_CODE = os.environ.get("SINGLEBOX_DINGTALK_ROBOT_CODE", "")
_DT_CARD_TEMPLATE_ID = os.environ.get("SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID", "")
_DT_ACCOUNT_ID = os.environ.get("SINGLEBOX_DINGTALK_ACCOUNT_ID", _USER_ID)

_FRONTEND_URL = os.environ.get(
    "SINGLEBOX_FRONTEND_URL",
    "http://agentclaw-local.stable.alipay.net:8000",
)

# ─── mock 任务数据 ────────────────────────────────────────────────────────
_MOCK_TASKS: list[dict] = [
    {
        "task_id": f"discover_notify_{_USER_ID}_{_TODAY}",
        "bot_id": "e2e-bot",
        "owner_id": _USER_ID,
        "dt": _TODAY,
        "project_name": "存储行业尽调报告",
        "description": "AI 基础设施驱动下,企业级与数据中心存储行业的最新变化。",
        "business_scenario": "投资尽调 — 产出系统性的投资判断报告。",
        "discovery_basis": "用户近一周频繁搜索存储行业相关信息。",
        "work_item_url": "https://project.alipay.com/workitem/123456",
        "priority": "high",
        "discovered_at": f"{_TODAY}T10:00:00Z",
        "status": "pending_confirmation",
    },
]

_DATA_FILE = Path(__file__).resolve()
for _ in range(8):
    _DATA_FILE = _DATA_FILE.parent
_DATA_FILE = _DATA_FILE / "scripts" / ".dependencies" / "data" / "discovered_tasks.db"


def _write_mock_data(bot_id: str, owner_id: str) -> None:
    """写 mock 任务数据到 discovered_tasks.db。"""
    from agentclaw.community.core.task.task_discovery.task_reader import (
        init_discovered_tasks_db,
    )

    tasks = [
        {**t, "task_id": t["task_id"].replace("e2e-bot", bot_id), "bot_id": bot_id, "owner_id": owner_id}
        for t in _MOCK_TASKS
    ]
    init_discovered_tasks_db(str(_DATA_FILE), tasks)
    print(f"[setup] mock 数据已写入 {_DATA_FILE} ({len(tasks)} tasks, bot={bot_id})")


# ─── DingTalk 卡片 ────────────────────────────────────────────────────────

def _build_card_data(
    *,
    workitem_name: str = "",
    workitem_bg: str = "",
    session_url: str = "",
) -> str:
    """构建钉钉卡片 card_data JSON, session_url 带上 discover 创建的 session 链接。"""
    return json.dumps({
        "click": "",
        "card_name": "为你发现以下任务",
        "session_url": session_url,  # 直接传 session_url, 点击跳转到 session
        "workitem_name": workitem_name,
        "workitem_bg": workitem_bg,
    }, ensure_ascii=False)


def _send_dingtalk_card(
    card_data: str,
    *,
    ak_id: str,
    ak_secret: str,
    robot_code: str,
    card_template_id: str,
    account_id: str,
    card_biz_id: str = "",
) -> dict:
    """调用钉钉 SDK 发送交互卡片, 返回响应 body dict。"""
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models
    from alipay_antdingopensdk_client import models as antdingopen_models
    from alipay_antdingopensdk_client.client import Client as antdingopenClient

    config = open_api_models.Config()
    config.access_key_id = ak_id
    config.access_key_secret = ak_secret
    client = antdingopenClient(config)

    headers = antdingopen_models.HttpHeader()
    headers.account_context = antdingopen_models.AccountContext(account_id=account_id)

    req = antdingopen_models.SendRobotInteractiveCardRequest()
    req.card_template_id = card_template_id
    req.robot_code = robot_code
    req.card_biz_id = card_biz_id or f"discover_notify_{int(time.time())}"
    req.card_data = card_data
    req.user_id = account_id

    resp = client.send_robot_interactive_card_with_options(
        req, headers, util_models.RuntimeOptions(),
    )
    biz_resp = resp.body
    return biz_resp.to_map() if hasattr(biz_resp, "to_map") else {"raw": str(biz_resp)}


# ─── 核心流程 ─────────────────────────────────────────────────────────────

async def _discover_and_notify(
    *,
    target_bot_id: str | None = None,
    target_user_id: str | None = None,
) -> dict:
    """主流程: 查 bot → 写 mock → scheduled-trigger → 拿 session_url → 发钉钉卡片。

    Args:
        target_bot_id: 指定 bot_id，不传则取第一个 ACTIVE bot。
        target_user_id: 钉钉通知的目标用户 ID，不传则用 bot owner_id。
    """
    result: dict = {"steps": [], "session_url": None, "dingtalk_resp": None}

    # 1) 查已有 bot
    async with httpx.AsyncClient(timeout=30.0, headers=_HDRS) as cli:
        bots: list[dict] = []
        for endpoint in [
            f"{_BACKEND}/api/bots/by-owner-or-collaborator",
            f"{_BACKEND}/api/bots",
        ]:
            try:
                r = await cli.get(endpoint, params={"user_id": _USER_ID})
                if r.status_code == 200:
                    bots = (r.json().get("data") or {}).get("items") or []
                    if bots:
                        break
            except Exception:
                continue

    if not bots:
        result["error"] = "singlebox 未 provision 任何 bot, 请先 start all"
        return result

    # 优先用指定的 bot_id，否则取第一个 ACTIVE bot
    if target_bot_id:
        matched = [b for b in bots if b.get("bot_id") == target_bot_id]
        if not matched:
            result["error"] = f"未找到 bot_id={target_bot_id}"
            return result
        bot = matched[0]
    else:
        active_bots = [b for b in bots if b.get("status") == "ACTIVE"]
        bot = active_bots[0] if active_bots else bots[0]
    bot_id = bot["bot_id"]
    owner_id = bot.get("owner_id", _USER_ID)
    # 钉钉通知用户：优先用参数传入，否则用 bot owner_id
    notify_user_id = target_user_id or owner_id
    result["bot_id"] = bot_id
    result["owner_id"] = owner_id
    print(f"[1] bot_id={bot_id} owner_id={owner_id}")

    # 2) 写 mock 数据
    _write_mock_data(bot_id, owner_id)
    result["steps"].append("mock_data_written")

    # 3) scheduled-trigger → 拿 session_url
    async with httpx.AsyncClient(timeout=120.0, headers=_HDRS) as cli:
        r = await cli.post(
            f"{_BACKEND}/api/v1/collaboration/tasks/discovery/scheduled-trigger",
        )
        body = r.json() if r.status_code == 200 else {}

    print(f"[2] scheduled-trigger: success={body.get('success')} "
          f"total_discovered={body.get('total_discovered')}")

    if not body.get("success"):
        result["error"] = f"scheduled-trigger 未成功: {body.get('message')}"
        return result

    results = body.get("results") or []
    our_results = [r for r in results if r.get("bot_id") == bot_id]
    if not our_results:
        result["error"] = "scheduled-trigger 未返回本 bot 的结果"
        return result

    discover_result = our_results[0]
    if not discover_result.get("success"):
        result["error"] = f"discover 未成功: {discover_result.get('error')}"
        return result

    session_key = discover_result.get("session_id")
    full_session_id = f"agent:main:{session_key}"
    from urllib.parse import quote
    session_url = f"{_FRONTEND_URL.rstrip('/')}/assistant?botId={bot_id}&sessionId={quote(full_session_id, safe='')}"
    result["session_id"] = full_session_id
    result["session_url"] = session_url
    result["steps"].append("discover_done")
    print(f"[3] discover 成功: session_id={full_session_id}")
    print(f"    session_url={session_url}")

    if not session_url:
        result["error"] = "session_url 为空 — 无法投递带链接的钉钉卡片"
        return result

    # 4) 构建带 session_url 的 card_data → 发钉钉卡片
    mock_task = _MOCK_TASKS[0]
    card_data = _build_card_data(
        workitem_name=mock_task["project_name"],
        workitem_bg=mock_task["description"],
        session_url=session_url,
    )
    print(f"[4] 发送钉钉卡片: session_url={session_url}")
    print(f"    workitem={mock_task['project_name']} notify_user={notify_user_id}")
    print(f"    card_data={card_data}")

    resp = _send_dingtalk_card(
        card_data,
        ak_id=_DT_AK_ID,
        ak_secret=_DT_AK_SECRET,
        robot_code=_DT_ROBOT_CODE,
        card_template_id=_DT_CARD_TEMPLATE_ID,
        account_id=notify_user_id,
        card_biz_id=f"discover_notify_{bot_id}_{int(time.time())}",
    )
    result["dingtalk_resp"] = resp
    result["steps"].append("dingtalk_sent")
    print(f"[5] 钉钉响应: {json.dumps(resp, ensure_ascii=False)}")
    print(f"\n✓ 端到端完成: discover → session 创建 → 钉钉卡片(带 session_url) 发送")
    print(f"  点击卡片将跳转到: {session_url}")

    # 6) 再创建 5 个新 session（模拟多次 discover 场景）
    print(f"\n[6] 再创建 5 个新 session...")
    extra_sessions: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0, headers=_HDRS) as cli:
        # 查 bot binding → 拿 engine target
        bot_resp = await cli.get(f"{_BACKEND}/api/bots/{bot_id}")
        binding_id = None
        if bot_resp.status_code == 200:
            bot_data = (bot_resp.json().get("data") or {})
            binding_id = bot_data.get("binding_id") or bot_data.get("engine_binding_id")

        if binding_id:
            conn_resp = await cli.get(f"{_BACKEND}/api/v1/devices/{binding_id}/connection")
            if conn_resp.status_code == 200:
                conn_data = (conn_resp.json().get("data") or {})
                base_url = conn_data.get("base_url") or conn_data.get("target", "")
                if base_url and not base_url.startswith("http"):
                    base_url = f"http://{base_url}"
                if base_url:
                    engine_headers = {"x-user-id": owner_id, "Content-Type": "application/json"}
                    for i in range(5):
                        idx = i + 1
                        await asyncio.sleep(5)  # 每隔 5 秒创建一个
                        custom_key = f"cron_{int(session_key.replace('cron_', '')) + idx:03d}"
                        titles = [
                            "存储行业最新动态跟踪",
                            "竞品技术方案对比分析",
                            "客户反馈问题汇总处理",
                            "本季度项目进度复盘",
                            "团队周会议题整理",
                        ]
                        session_body = {
                            "title": titles[i],
                            "user_id": owner_id,
                            "agent_id": bot_id,
                            "session_key": custom_key,
                            "extInfo": {"source": "task_discovery", "index": idx},
                        }
                        try:
                            r = await cli.post(
                                f"{base_url}/api/sessions",
                                json=session_body,
                                headers=engine_headers,
                            )
                            if r.status_code == 200:
                                sdata = r.json().get("data", {})
                                raw_key = sdata.get("id") or sdata.get("session_id", "") or custom_key
                                full_id = f"agent:main:{raw_key}"
                                surl = f"{_FRONTEND_URL.rstrip('/')}/assistant?botId={bot_id}&sessionId={quote(full_id, safe='')}"
                                extra_sessions.append({"session_id": full_id, "session_url": surl})
                                print(f"  session #{idx} (+{5*(i+1)}s): id={full_id} title={titles[i]}")
                                print(f"    url={surl}")
                            else:
                                print(f"  session #{idx} (+{5*(i+1)}s): HTTP {r.status_code} — {r.text[:100]}")
                        except Exception as e:
                            print(f"  session #{idx} (+{5*(i+1)}s): error — {e}")

    result["extra_sessions"] = extra_sessions
    if extra_sessions:
        result["steps"].append("extra_sessions_created")
        print(f"\n✓ 共创建 {len(extra_sessions)} 个额外 session")
        all_sessions = [{"session_id": full_session_id, "session_url": session_url}] + extra_sessions
        print(f"  总计 {len(all_sessions)} 个 session:")
        for s in all_sessions:
            print(f"    - {s['session_id']}: {s['session_url']}")

    return result


# ─── unittest 入口 ────────────────────────────────────────────────────────

class TestDiscoverAndNotify(unittest.TestCase):
    """端到端: scheduled-trigger → discover → 拿 session_url → 钉钉卡片带 session_url 投递。"""

    def test_discover_and_notify(self) -> None:
        """完整流程测试。"""
        if not os.environ.get("SINGLEBOX_CRON_E2E", "").strip() in {"1", "true"}:
            self.skipTest("设置 SINGLEBOX_CRON_E2E=1 启用")
        if not _DT_LIVE:
            self.skipTest("设置 SINGLEBOX_DINGTALK_E2E=1 启用")
        self.assertTrue(_DT_AK_ID, "SINGLEBOX_DINGTALK_AK_ID 未设")
        self.assertTrue(_DT_AK_SECRET, "SINGLEBOX_DINGTALK_AK_SECRET 未设")
        self.assertTrue(_DT_ROBOT_CODE, "SINGLEBOX_DINGTALK_ROBOT_CODE 未设")
        self.assertTrue(_DT_CARD_TEMPLATE_ID, "SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID 未设")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_discover_and_notify())
        finally:
            loop.close()

        # 断言
        self.assertNotIn("error", result, f"流程出错: {result.get('error')}")
        self.assertIsNotNone(result.get("session_url"), "session_url 为空")
        self.assertIsNotNone(result.get("dingtalk_resp"), "钉钉响应为空")
        self.assertIn("dingtalk_sent", result.get("steps", []))


# ─── 直接运行入口 ─────────────────────────────────────────────────────────

def _run_direct() -> None:
    """直接 python test_discover_and_notify_e2e.py 运行。

    可选参数:
      --bot-id=xxx       指定目标 bot_id
      --notify-user=xxx  指定钉钉通知用户 ID（默认用 bot owner_id）
    """
    import argparse

    parser = argparse.ArgumentParser(description="discover → 钉钉通知 e2e")
    parser.add_argument("--bot-id", default=None, help="指定目标 bot_id")
    parser.add_argument("--notify-user", default=None, help="钉钉通知用户 ID（默认用 bot owner_id）")
    args = parser.parse_args()

    if not os.environ.get("SINGLEBOX_CRON_E2E", "").strip() in {"1", "true"}:
        print("设置 SINGLEBOX_CRON_E2E=1 启用")
        sys.exit(1)
    if not _DT_LIVE:
        print("设置 SINGLEBOX_DINGTALK_E2E=1 启用")
        sys.exit(1)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_discover_and_notify(
            target_bot_id=args.bot_id,
            target_user_id=args.notify_user,
        ))
    finally:
        loop.close()

    if "error" in result:
        print(f"\n✗ 失败: {result['error']}")
        sys.exit(1)
    print(f"\n✓ 成功")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    _run_direct()