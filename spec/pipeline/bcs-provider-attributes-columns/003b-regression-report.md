# BCS Provider Bot 属性物理列修复：本地回归报告

## 结果

PASS。

| 命令 | 结果 |
|---|---|
| `cargo test -p bcs-bot-store --test conformance_bot_control_plane_repo` | 13 passed, 0 failed |
| `cargo test -p bcs-app-bot` | 20 passed, 0 failed |
| `cargo test -p bcs-http --test provider_routes_contract` | 39 passed, 0 failed |
| `git diff --check` | 通过 |

未执行 Engine 本地回归：本次仅变更 BCS 持久化仓储；上述测试覆盖了仓储、应用服务和
Provider HTTP 合约边界。
