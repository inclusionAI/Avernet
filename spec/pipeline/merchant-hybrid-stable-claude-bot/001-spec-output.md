---
agent: codex
status: approved-by-request
created: 2026-08-14
---

# Merchant hybrid Claude BCS identity stability

## 需求

`merchant_hybrid` 的 Claude Provider Bot 必须使用 BCS ID `平台数据分析`，与 OpenClaw Bot 的命名式 ID 一致。正常的 `start`、`stop`、`restart` 不得删除该 Provider 注册或使其退出既有群聊。

## 实施计划

1. 将 Claude Provider 的内部 `provider_bot_ref` 改为稳定的技术标识，避免依赖每次可能变化的后端 Bot ID。
2. 为本地 BCS 配置受限的 Provider Bot ID 覆盖规则：只在 Provider 名称、创建者和内部引用三项完全匹配时，允许使用指定的 Bot ID。
3. 启动时验证并复用健康的注册；仅当 BCS 中注册已丢失，或检测到历史随机 ID 时才清除本地状态并重新注册。历史随机 ID 迁移时有意删除旧注册，因 BCS 群成员需要重新拉取目标 Bot。
4. 停止时只停止 bridge，不再注销 BCS Provider Bot。

## 验收与验证

- Shell 回归覆盖稳定的内部引用、正常 stop 不注销、健康注册在下一次 start 中复用。
- BCS HTTP 合同测试覆盖精确受信任规则下的指定 Bot ID，且不匹配请求仍使用随机 ID。
- 对变更脚本执行 Bash 语法检查，并运行受影响的 shell/Rust 测试。
- 本次不执行 `singlebox.sh start`、`stop` 或 `restart`，不影响当前运行服务。
