# 真实 BCS 断线重连证据

观测日期：2026-11-02；BCS：local standalone BCS 0.1.4-beta.3。

在真实本机 BCS 上创建临时测试 Bot，连接成功后主动关闭客户端 WebSocket；客户端使用持久化 token 自动重连。以下为脱敏事件（无 token/路径）：

| 时间 | 事件 | 说明 |
| --- | --- | --- |
| 2026-11-02 01:22:06.374 | connected | is_new=True, protocol=3 |
| 2026-11-02 01:22:07.378 | forced_close | 测试主动关闭客户端 WebSocket，触发真实 BCS 断线 |
| 2026-11-02 01:22:07.378 | runs_cancelled | 连接关闭时清空活跃 run |
| 2026-11-02 01:22:07.379 | connection_error | ConnectionClosedError |
| 2026-11-02 01:22:08.389 | reconnected | same_identity=True |

结论：客户端在真实 BCS 上被主动断开后，使用持久化 token 在约 1 秒内以同一 bot 身份重连（is_new=false）。

限制：

- 这是连接层验证，未绑定游戏 run；不等于游戏内断线恢复。
- 没有在真实游戏节点中途断线；主持人/其他玩家行为未参与。

脱敏：不含 token、密钥、本机路径；bot_uuid 仅保留哈希。
