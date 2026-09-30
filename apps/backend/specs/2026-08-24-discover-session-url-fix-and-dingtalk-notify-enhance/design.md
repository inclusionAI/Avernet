# 技术设计 — discover session_url 修复 + 钉钉通知增强

## 1. 改动范围

```
session_initiator.py          (修改)  URL 格式 + title [DreamMode] + update 调用
test_discover_and_notify_e2e.py (新增) 端到端测试脚本
```

## 2. session_url 格式变更

### Before

```python
def _build_session_url(self, session_id: str, agent_id: str) -> str:
    base = self._frontend_url.rstrip("/")
    return (
        f"{base}/bcn/chat/session"
        f"?bot_uuid={agent_id}&id={agent_id}&session={session_id}"
    )
```

问题：
- 路径 `/bcn/chat/session` 不存在
- 参数名 `bot_uuid` / `id` / `session` 与前端路由不匹配

### After

```python
def _build_session_url(self, session_id: str, agent_id: str) -> str:
    from urllib.parse import quote
    base = self._frontend_url.rstrip("/")
    encoded_sid = quote(session_id, safe="")
    return f"{base}/assistant?botId={agent_id}&sessionId={encoded_sid}"
```

前端路由：`/assistant?botId=xxx&sessionId=xxx`

## 3. title [DreamMode] 标记

### title 构造

```python
title = (
    f"[DreamMode] 发现 {task_count} 件可能有意义的事情"
    if task_count > 1
    else f"[DreamMode] {first_task.project_name}"
)
```

### title 更新流程

```
Step 1: POST /api/sessions (via cron_relay.forward_request) → engine 创建 session
Step 2: _extract_engine_target → 拿到 engine HTTP 地址 (e.g. localhost:20019)
Step 2.5: POST http://{target}/api/sessions/{session_id}/update?title={title} (直接 httpx)
Step 3: WebSocket chat.send 注入发现消息
```

title 更新失败不阻断主流程（non-fatal）。

## 4. 测试脚本架构

```
test_discover_and_notify_e2e.py
├── _discover_and_notify(target_bot_id, target_user_id)
│   ├── 1. 查 bot → 支持指定 bot_id
│   ├── 2. 写 mock 任务数据 (init_discovered_tasks_db)
│   ├── 3. scheduled-trigger → 拿 session_url
│   ├── 4. 构建带 session_url 的 card_data → 发钉钉卡片
│   │   └── notify_user_id = target_user_id or owner_id
│   └── 5. 每隔 5 秒创建 5 个额外 session（直接调 engine API）
│       └── titles: 存储行业最新动态跟踪 / 竞品技术方案对比分析 / ...
├── TestDiscoverAndNotify (unittest)
│   └── test_discover_and_notify
└── _run_direct() — argparse 解析 --bot-id / --notify-user
```

## 5. session ID 格式

所有 session ID 统一显示为 `agent:main:xxx` 格式：

| 来源 | engine 返回 | 脚本显示 |
|------|------------|---------|
| discover (cron_relay) | `cron_001` | `agent:main:cron_001` |
| 直接 engine API | `session:uuid:user:440718` | `agent:main:session:uuid:user:440718` |

URL 中 sessionId 格式（URL encoded）：
```
agent%3Amain%3Acron_001
agent%3Amain%3Asession%3Auuid%3Auser%3A440718
```