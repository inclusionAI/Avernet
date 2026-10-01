# 任务清单 — discover session_url 修复 + 钉钉通知增强

## 已完成

- [x] T1: 修复 `_build_session_url` URL 格式 — `/assistant?botId=&sessionId=` + URL encode
  - 文件: `session_initiator.py:335-341`

- [x] T2: discover session title 加 `[DreamMode]` 前缀
  - 文件: `session_initiator.py:110-113`

- [x] T3: session 创建后通过 engine API 更新 title
  - 文件: `session_initiator.py:158-181` (Step 2.5)
  - 注意: 不能用 cron_relay（只处理 /api/cron*），需直接 httpx 调 engine target

- [x] T4: 新增 `test_discover_and_notify_e2e.py` 端到端测试脚本
  - 支持 `--bot-id` / `--notify-user` 命令行参数
  - discover → 钉钉通知 → 5 个额外 session（间隔 5 秒）
  - session ID 统一 `agent:main:xxx` 格式
  - 额外 session 使用工作标题

- [x] T5: singlebox 启动时设置 `FRONTEND_URL` 环境变量
  - `export FRONTEND_URL=http://agentclaw-local.stable.alipay.net:8000`

## 验证

- [x] session_url 格式正确: `http://agentclaw-local.stable.alipay.net:8000/assistant?botId=xxx&sessionId=agent%3Amain%3Acron_001`
- [x] engine 端 title 更新成功: `[DreamMode] 存储行业尽调报告_cron_001`
- [x] 钉钉卡片发送成功 (processQueryKey 返回)
- [x] 5 个额外 session 按 5 秒间隔创建，带工作标题