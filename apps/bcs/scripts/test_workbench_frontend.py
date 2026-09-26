#!/usr/bin/env python3
"""
BCS 前端对接测试脚本
模拟 AI 工作台前端，按照接口文档完整测试群聊流程：
1. 创建群聊
2. 查询群详情
3. 建立多个 WebSocket 连接（模拟多个前端观察者）
4. 从其中一个前端发送消息（代表某个 bot）
5. 验证所有前端都能收到 bot 回复
6. 查询群历史消息
"""

import asyncio
import json
import os
import uuid
import time
import sys
import ssl
import httpx
import websockets

BCS_URL = os.environ.get("BCS_URL", "http://127.0.0.1:21000")
BCS_WS_URL = os.environ.get("BCS_WS_URL", "ws://127.0.0.1:21000/ws")

BCS_COOKIE = os.environ.get("BCS_COOKIE", "")

# HTTP headers for authentication
HTTP_HEADERS = {"Cookie": BCS_COOKIE} if BCS_COOKIE else {}
WS_HEADERS = {"Cookie": BCS_COOKIE} if BCS_COOKIE else {}

# SSL context that ignores certificate verification (for pre-prod environment)
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# HTTP client with TLS verification disabled
HTTP_CLIENT = httpx.Client(verify=False)

BOT_COORDINATOR = os.environ.get("BOT_COORDINATOR_NAME", "Coordinator Bot")
BOT_CONSULTANT_1 = os.environ.get("BOT_CONSULTANT_1_NAME", "Legal Bot")
BOT_CONSULTANT_2 = os.environ.get("BOT_CONSULTANT_2_NAME", "Database Bot")

# 模拟 3 个前端 WebSocket 连接（纯观察者，发消息时用真实 bot_id）
FRONTEND_COUNT = 3

# 颜色输出
GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[1;33m"
CYAN   = "\033[0;36m"
GRAY   = "\033[0;90m"
NC     = "\033[0m"

def ok(msg):   print(f"  {GREEN}✓{NC} {msg}")
def fail(msg): print(f"  {RED}✗{NC} {msg}"); sys.exit(1)
def info(msg): print(f"  {CYAN}→{NC} {msg}")
def warn(msg): print(f"  {YELLOW}⚠{NC} {msg}")
def dim(msg):  print(f"  {GRAY}{msg}{NC}")


# ============================================================================
# Health Check
# ============================================================================

def check_health():
    """检查 BCS 健康状态"""
    print(f"\n{CYAN}{'='*60}{NC}")
    print(f"{CYAN}  BCS Health Check{NC}")
    print(f"{CYAN}{'='*60}{NC}")
    print(f"  BCS URL: {BCS_URL}")

    try:
        r = HTTP_CLIENT.get(f"{BCS_URL}/health", headers=HTTP_HEADERS, timeout=10)
        if r.status_code == 200:
            ok(f"BCS is healthy at {BCS_URL}")
            dim(f"  Response: {r.text}")
            return True
        else:
            fail(f"BCS health check failed: {r.status_code} {r.text}")
            return False
    except Exception as e:
        fail(f"BCS health check failed: {e}")
        return False


def usage():
    print(f"""
{CYAN}BCS 前端对接测试脚本{NC}

Usage:
  python3 test_workbench_frontend.py [command]

Commands:
  health    检查 BCS 健康状态
  test      运行完整测试流程（默认）
  help      显示帮助信息
""")


# ============================================================================
# Step 1: 查询已注册的 bot
# ============================================================================

def step_list_bots() -> list[dict]:
    """查询已注册的 bot 列表"""
    print(f"\n{CYAN}[Step 1] 查询已注册的 bot{NC}")
    info(f"GET /bots")
    r = HTTP_CLIENT.get(f"{BCS_URL}/bots", headers=HTTP_HEADERS, timeout=10)
    if r.status_code != 200:
        fail(f"GET /bots 失败: {r.status_code} {r.text}")
    bots = r.json()
    if not bots:
        fail("没有已注册的 bot，请先启动并 onboard bot")
    ok(f"找到 {len(bots)} 个已注册的 bot")
    for b in bots:
        name = b.get("name") or b.get("capabilities", {}).get("name") or "?"
        bot_uuid = b.get("bot_uuid", "?")
        dim(f"  - {name} ({bot_uuid})")
    return bots


# ============================================================================
# Step 2: 创建群聊
# ============================================================================

def step_create_group(coordinator: str, coordinator_name: str, consultants: list[str], consultant_names: list[str]) -> str:
    print(f"\n{CYAN}[Step 2] 创建群聊{NC}")
    payload = {
        "driver_bot": coordinator,
        "participants": [
            {"bot_uuid": coordinator, "bot_name": coordinator_name, "role": "driver"},
        ] + [
            {"bot_uuid": c, "bot_name": n, "role": "consultant"}
            for c, n in zip(consultants, consultant_names)
        ],
        "label": "前端对接测试群",
    }
    info(f"POST /groups  coordinator={coordinator}  consultants={consultants}")
    r = HTTP_CLIENT.post(f"{BCS_URL}/groups", json=payload, headers=HTTP_HEADERS, timeout=10)
    if r.status_code != 200:
        fail(f"创建群失败: {r.status_code} {r.text}")
    data = r.json()
    group_id = data.get("group_id") or data.get("id")
    if not group_id:
        fail(f"响应中没有 group_id: {data}")
    ok(f"群创建成功: group_id={group_id}")
    dim(f"  响应: {json.dumps(data, ensure_ascii=False)}")
    return group_id


# ============================================================================
# Step 3: 查询群详情
# ============================================================================

def step_get_group(group_id: str):
    print(f"\n{CYAN}[Step 3] 查询群详情{NC}")
    r = HTTP_CLIENT.get(f"{BCS_URL}/groups/{group_id}", headers=HTTP_HEADERS)
    if r.status_code != 200:
        fail(f"GET /groups/{group_id} 失败: {r.status_code} {r.text}")
    data = r.json()
    ok(f"群详情获取成功:")
    dim(f"  {json.dumps(data, ensure_ascii=False, indent=2)}")
    return data


# ============================================================================
# Step 4-6: WebSocket 多前端测试
# ============================================================================

class FrontendClient:
    """模拟一个前端 WebSocket 连接（纯观察者）"""

    def __init__(self, index: int, group_id: str):
        self.name = f"前端-{index+1}"
        self.group_id = group_id
        self.ws = None
        self.received_events: list[dict] = []
        self.final_received = asyncio.Event()
        self.bot_finals: dict[str, asyncio.Event] = {}
        self._req_counter = 0
        self._pending: dict[str, asyncio.Future] = {}

    def expect_bot_final(self, bot_uuid: str) -> asyncio.Event:
        if bot_uuid not in self.bot_finals:
            self.bot_finals[bot_uuid] = asyncio.Event()
        return self.bot_finals[bot_uuid]

    def reset_finals(self):
        self.final_received.clear()
        self.bot_finals.clear()

    def _next_req_id(self) -> str:
        self._req_counter += 1
        return f"fe{self._req_counter:03d}"

    async def connect(self):
        self.ws = await websockets.connect(BCS_WS_URL, additional_headers=WS_HEADERS)
        info(f"[{self.name}] WebSocket 已连接")

    async def subscribe(self):
        req_id = self._next_req_id()
        frame = {
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {"group_id": self.group_id},
        }
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps(frame))
        res = await asyncio.wait_for(fut, timeout=5)
        if not res.get("ok"):
            fail(f"[{self.name}] connect 失败: {res}")
        ok(f"[{self.name}] 已订阅群 {self.group_id}")

    async def send_message(self, message: str, coordinator_bot: str, sender_bot_id: str = None):
        """发送群聊消息，sender_bot_id 是群里真实 bot 的 uuid"""
        req_id = self._next_req_id()
        frame = {
            "type": "req",
            "id": req_id,
            "method": "chat.send",
            "params": {
                "sessionKey": "main",
                "message": message,
                "group_id": self.group_id,
                "bot_uuid": coordinator_bot,
                "bot_id": sender_bot_id or coordinator_bot,
                "timeoutMs": 120000,
            },
        }
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps(frame))
        res = await asyncio.wait_for(fut, timeout=10)
        if not res.get("ok"):
            fail(f"[{self.name}] chat.send 失败: {res}")
        run_id = res.get("payload", {}).get("runId") or res.get("payload", {}).get("run_id")
        ok(f"[{self.name}] 消息已发送: runId={run_id}")
        return run_id

    async def listen(self, stop_event: asyncio.Event):
        """持续接收消息，直到 stop_event 被设置"""
        try:
            async for raw in self.ws:
                frame = json.loads(raw)
                ftype = frame.get("type")

                # 响应帧 → 唤醒 pending future
                if ftype == "res":
                    req_id = frame.get("id")
                    if req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if not fut.done():
                            fut.set_result(frame)
                    continue

                # 事件帧
                if ftype == "event":
                    self.received_events.append(frame)
                    event_name = frame.get("event")
                    payload = frame.get("payload", {})
                    bot_uuid = frame.get("bot_uuid", "?")
                    state = payload.get("state") or payload.get("stream")

                    if event_name == "chat.event" or (event_name == "chat" and state):
                        if state == "delta":
                            text = ""
                            msg = payload.get("message", {})
                            for block in (msg.get("content") or []):
                                if isinstance(block, dict):
                                    text += block.get("text", "")
                                elif isinstance(block, str):
                                    text += block
                            dim(f"  [{self.name}] delta from {bot_uuid}: {text[:60]}")
                        elif state == "final":
                            msg = payload.get("message", {})
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                content = "".join(
                                    b.get("text", "") if isinstance(b, dict) else str(b)
                                    for b in content
                                )
                            ok(f"[{self.name}] ✦ final from {bot_uuid}: {str(content)[:80]}")
                            self.final_received.set()
                            # 也设置该 bot 的专属 final 事件
                            if bot_uuid in self.bot_finals:
                                self.bot_finals[bot_uuid].set()
                    elif event_name == "agent":
                        stream = payload.get("stream")
                        data = payload.get("data", {})
                        if stream == "tool":
                            phase = data.get("phase")
                            tool_name = data.get("name", "?")
                            dim(f"  [{self.name}] tool {phase}: {tool_name}")
                        elif stream == "thinking":
                            dim(f"  [{self.name}] thinking...")

                if stop_event.is_set():
                    break
        except websockets.exceptions.ConnectionClosed:
            pass

    async def close(self):
        if self.ws:
            await self.ws.close()


async def step_websocket_test(group_id: str, coordinator: str):
    print(f"\n{CYAN}[Step 4-6] WebSocket 多前端测试{NC}")

    # 创建 3 个前端客户端
    clients = [FrontendClient(i, group_id) for i in range(FRONTEND_COUNT)]

    # 连接
    info("连接 WebSocket...")
    for c in clients:
        await c.connect()

    # 先启动所有监听任务
    stop_event = asyncio.Event()
    listen_tasks = [
        asyncio.create_task(c.listen(stop_event))
        for c in clients
    ]

    # 让 listen 任务跑起来
    await asyncio.sleep(0.1)

    # 再订阅群聊
    info("订阅群聊...")
    for c in clients:
        await c.subscribe()

    # 从第一个前端发送消息
    sender = clients[0]
    message = "你好，请简单介绍一下你自己，用一句话回答。"
    info(f"\n从 [{sender.name}] 发送消息（代表 coordinator {coordinator}）: {message}")
    await sender.send_message(message, coordinator, sender_bot_id=coordinator)

    # 等待所有前端都收到 final 事件（最多 90 秒）
    info("等待所有前端收到 bot 回复...")
    timeout = 90
    deadline = time.time() + timeout
    while time.time() < deadline:
        all_received = all(c.final_received.is_set() for c in clients)
        if all_received:
            break
        await asyncio.sleep(0.5)

    stop_event.set()
    for t in listen_tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    # 验证结果
    print()
    all_ok = True
    for c in clients:
        if c.final_received.is_set():
            ok(f"[{c.name}] 收到了 bot 的 final 回复 ✓")
        else:
            warn(f"[{c.name}] 未收到 final 回复（超时）")
            all_ok = False

    if all_ok:
        ok("所有前端均收到 bot 回复！")
    else:
        warn("部分前端未收到回复，可能是超时或 bot 未响应")

    # 关闭连接
    for c in clients:
        await c.close()

    return all_ok


# ============================================================================
# Step 7: @mention 路由测试 —— 用户消息直接 @mention bot
# ============================================================================

async def step_mention_routing_test(group_id: str, coordinator: str, consultants: list[str]):
    print(f"\n{CYAN}[Step 7] @mention 路由测试{NC}")
    info("验证：coordinator 回复中 @mention 另一个 bot，被 @mention 的 bot 应收到 chat.send 并回复")

    if not consultants:
        warn("没有 consultant bot，跳过 @mention 测试")
        return False

    target_bot = consultants[0]
    info(f"目标 consultant bot: {target_bot}")

    # 复用一个前端连接
    client = FrontendClient(0, group_id)
    await client.connect()

    stop_event = asyncio.Event()
    listen_task = asyncio.create_task(client.listen(stop_event))
    await asyncio.sleep(0.1)
    await client.subscribe()

    # 注册对 coordinator 和 target_bot 的 final 事件
    coordinator_final = client.expect_bot_final(coordinator)
    target_final = client.expect_bot_final(target_bot)

    # 发送一条直接 @mention target_bot 的消息，验证路由到指定 bot
    message = f"@{target_bot} 请用一句话介绍你自己。"
    info(f"发送消息（直接 @mention consultant）: {message}")
    await client.send_message(message, coordinator, sender_bot_id=coordinator)

    # 等待 coordinator 先回复（最多 60 秒）
    info("等待 coordinator 回复...")
    timeout = 60
    try:
        await asyncio.wait_for(coordinator_final.wait(), timeout=timeout)
        ok(f"coordinator ({coordinator}) 已回复")
    except asyncio.TimeoutError:
        warn(f"coordinator 未在 {timeout}s 内回复")
        stop_event.set()
        listen_task.cancel()
        try:
            await listen_task
        except asyncio.CancelledError:
            pass
        await client.close()
        return False

    # 等待 target_bot 被 @mention 后回复（再等 60 秒）
    info(f"等待 @mention 的 bot ({target_bot}) 回复...")
    try:
        await asyncio.wait_for(target_final.wait(), timeout=timeout)
        ok(f"@mention 的 bot ({target_bot}) 已回复 ✓")
        result = True
    except asyncio.TimeoutError:
        warn(f"@mention 的 bot ({target_bot}) 未在 {timeout}s 内回复（可能 coordinator 没有在回复中 @mention 它）")
        result = False

    stop_event.set()
    listen_task.cancel()
    try:
        await listen_task
    except asyncio.CancelledError:
        pass
    await client.close()
    return result


# ============================================================================
# Step 8: bot 回复中 @mention 另一个 bot 的路由测试
# ============================================================================

async def step_bot_reply_mention_test(group_id: str, coordinator: str, coordinator_name: str,
                                       consultants: list[str], consultant_names: list[str]):
    print(f"\n{CYAN}[Step 8] bot 回复 @mention 路由测试{NC}")
    info("验证：coordinator 回复中包含 @botName，BCS 应将 chat.send 路由给被 @mention 的 bot")

    if not consultants:
        warn("没有 consultant bot，跳过测试")
        return False

    target_bot = consultants[0]
    target_name = consultant_names[0]
    info(f"coordinator: {coordinator_name} ({coordinator})")
    info(f"target bot:  {target_name} ({target_bot})")

    client = FrontendClient(0, group_id)
    await client.connect()

    stop_event = asyncio.Event()
    listen_task = asyncio.create_task(client.listen(stop_event))
    await asyncio.sleep(0.1)
    await client.subscribe()

    # 注册对 coordinator 和 target_bot 的 final 事件
    coordinator_final = client.expect_bot_final(coordinator)
    target_final = client.expect_bot_final(target_bot)

    # 发送消息，要求 coordinator 在回复中 @mention target bot
    message = f"请在你的回复中 @{target_name} 并请他用一句话介绍自己。"
    info(f"发送消息（要求 coordinator 在回复中 @mention）: {message}")
    await client.send_message(message, coordinator, sender_bot_id=coordinator)

    # 等待 coordinator 回复（最多 90 秒）
    info(f"等待 coordinator ({coordinator_name}) 回复...")
    try:
        await asyncio.wait_for(coordinator_final.wait(), timeout=90)
        ok(f"coordinator ({coordinator_name}) 已回复")
    except asyncio.TimeoutError:
        warn(f"coordinator 未在 90s 内回复")
        stop_event.set()
        listen_task.cancel()
        try:
            await listen_task
        except asyncio.CancelledError:
            pass
        await client.close()
        return False

    # 等待 target_bot 被 @mention 后收到 chat.send 并回复（再等 90 秒）
    info(f"等待被 @mention 的 bot ({target_name}) 回复...")
    try:
        await asyncio.wait_for(target_final.wait(), timeout=90)
        ok(f"被 @mention 的 bot ({target_name}) 已回复 ✓  @mention 路由正常！")
        result = True
    except asyncio.TimeoutError:
        warn(f"被 @mention 的 bot ({target_name}) 未在 90s 内回复")
        warn("可能原因：coordinator 回复中没有包含 @mention，或 BCS 未正确解析 @botName")
        result = False

    stop_event.set()
    listen_task.cancel()
    try:
        await listen_task
    except asyncio.CancelledError:
        pass
    await client.close()
    return result


# ============================================================================
# Step 9: 查询群历史消息
# ============================================================================

def step_get_messages(group_id: str):
    print(f"\n{CYAN}[Step 9] 查询群历史消息{NC}")
    # 等待一下确保消息已存储
    time.sleep(2)
    r = HTTP_CLIENT.get(f"{BCS_URL}/groups/{group_id}/messages", headers=HTTP_HEADERS, timeout=15)
    if r.status_code != 200:
        fail(f"GET /groups/{group_id}/messages 失败: {r.status_code} {r.text}")
    messages = r.json()
    dim(f"  原始响应条数: {len(messages)}  (若>1条且含bot回复则来自bot session，否则来自GroupSession fallback)")
    dim(f"  原始响应:\n{json.dumps(messages, ensure_ascii=False, indent=2)}")
    if not messages:
        warn("历史消息为空（可能 bot session 中还没有记录，或 fallback 到 GroupSession 也为空）")
        return
    ok(f"获取到 {len(messages)} 条历史消息:")
    for m in messages:
        sender = m.get("sender", "?")
        mtype = m.get("message_type", "?")
        content = m.get("content", "")
        if len(content) > 80:
            content = content[:80] + "..."
        dim(f"  [{mtype}] {sender}: {content}")


# ============================================================================
# Step 10: 查询 bot 所在的所有群
# ============================================================================

def step_get_bot_groups(bot_uuid: str, expected_group_id: str):
    """测试 GET /bots/{bot_uuid}/groups 接口

    验证：
    1. 接口能正常返回该 bot 参与的所有群
    2. 返回的群列表中包含当前群
    """
    print(f"\n{CYAN}[Step 10] 查询 bot 所在的所有群{NC}")
    info(f"GET /bots/{bot_uuid}/groups")

    r = HTTP_CLIENT.get(f"{BCS_URL}/bots/{bot_uuid}/groups", headers=HTTP_HEADERS, timeout=15)
    if r.status_code != 200:
        fail(f"GET /bots/{bot_uuid}/groups 失败: {r.status_code} {r.text}")

    data = r.json()
    dim(f"  响应: {json.dumps(data, ensure_ascii=False, indent=2)}")

    # 验证响应结构
    if "bot_uuid" not in data:
        fail(f"响应缺少 bot_uuid 字段")
    if "groups" not in data:
        fail(f"响应缺少 groups 字段")
    if "total" not in data:
        fail(f"响应缺少 total 字段")

    returned_bot_uuid = data["bot_uuid"]
    if returned_bot_uuid != bot_uuid:
        fail(f"返回的 bot_uuid ({returned_bot_uuid}) 与请求的 ({bot_uuid}) 不一致")

    groups = data["groups"]
    total = data["total"]

    if len(groups) != total:
        warn(f"groups 数组长度 ({len(groups)}) 与 total ({total}) 不一致")

    ok(f"bot {bot_uuid} 参与了 {total} 个群")

    # 查找当前群
    found = False
    for g in groups:
        if g.get("group_id") == expected_group_id:
            found = True
            ok(f"找到当前群: {expected_group_id}")

            # 验证群字段
            required_fields = ["group_id", "label", "coordinator_bot", "participants", "created_at", "updated_at"]
            for field in required_fields:
                if field not in g:
                    warn(f"群缺少字段: {field}")

            # 显示群信息
            dim(f"  群详情: label={g.get('label')} coordinator={g.get('coordinator_bot')}")
            dim(f"  参与者: {[p.get('bot_uuid') for p in g.get('participants', [])]}")
            break

    if not found:
        fail(f"返回的群列表中未找到当前群 {expected_group_id}")

    return data


# ============================================================================
# Main
# ============================================================================

async def main():
    print(f"\n{CYAN}{'='*60}{NC}")
    print(f"{CYAN}  BCS 前端对接测试{NC}")
    print(f"{CYAN}{'='*60}{NC}")

    # Step 1: 查询 bot
    bots = step_list_bots()

    if len(bots) < 2:
        fail("至少需要 2 个已注册的 bot")

    def bot_name(b: dict) -> str:
        return b.get("name") or b.get("capabilities", {}).get("name") or b["bot_uuid"]

    coordinator = bots[0]["bot_uuid"]
    coordinator_name = bot_name(bots[0])
    consultants = [b["bot_uuid"] for b in bots[1:3]]
    consultant_names = [bot_name(b) for b in bots[1:3]]
    info(f"coordinator: {coordinator_name} ({coordinator})")
    info(f"consultants: {list(zip(consultant_names, consultants))}")

    # Step 2: 创建群
    group_id = step_create_group(coordinator, coordinator_name, consultants, consultant_names)

    # Step 3: 查询群详情
    step_get_group(group_id)

    # Step 4-6: WebSocket 多前端测试
    await step_websocket_test(group_id, coordinator)

    # Step 7: 用户消息直接 @mention 路由测试
    await step_mention_routing_test(group_id, coordinator, consultants)

    # Step 8: bot 回复中 @mention 另一个 bot 的路由测试
    await step_bot_reply_mention_test(group_id, coordinator, coordinator_name, consultants, consultant_names)

    # Step 9: 查询历史消息
    step_get_messages(group_id)

    # Step 10: 查询 bot 所在的所有群（测试 coordinator 和第一个 consultant）
    step_get_bot_groups(coordinator, group_id)
    if consultants:
        step_get_bot_groups(consultants[0], group_id)

    print(f"\n{GREEN}{'='*60}{NC}")
    print(f"{GREEN}  测试完成！group_id={group_id}{NC}")
    print(f"{GREEN}{'='*60}{NC}\n")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "test"

    if command == "health":
        check_health()
    elif command == "test":
        asyncio.run(main())
    elif command in ["help", "-h", "--help"]:
        usage()
    else:
        print(f"{RED}Unknown command: {command}{NC}")
        usage()
        sys.exit(1)
