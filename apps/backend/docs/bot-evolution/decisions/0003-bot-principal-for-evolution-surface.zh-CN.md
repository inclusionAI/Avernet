# 以自身作用域权限允许 bot 主体访问进化接口面

> English version: [0003-bot-principal-for-evolution-surface.md](0003-bot-principal-for-evolution-surface.md)

状态：proposed（决策草案；接受后晋升到 `docs/adr/`）。

## 决策

OpenAPI v1 目前拒绝 `bot` 主体，并且 bot→owner 的回退机制是被有意移除的。本
决策记录**仅**允许 bot 访问新的进化接口面（基因组读取、经验、收件箱、运行请求和
执行者作业），使用显式的作用域而不是冒充所有者：

- `genome:read:self`、`experience:write:self`、`inbox:write:self`，用于 bot
  作用于自身；
- `run:request:self`，默认关闭，由所有者策略连同预算一起启用；
- `evolution:runner`，用于执行进化策略作业的 bot 或 worker，仅限于已注册的
  策略 id 以及它们已认领作业的输入。

`self` 绑定到凭证中的 bot 身份，永远不绑定到请求参数。没有任何 bot 作用域允许
晋升、回滚、修改策略、启用进化策略或访问另一个 bot 的基因组。凭证来自现有的
Passport/AgentPass 签发；网关以类型 `bot` 及其作用域签署主体；各服务通过授权
钩子检查作用域。

bot 通过 `avn` CLI 和一个随附的 skill 访问该接口面，二者由 Manifest
`cli_tools` 交付，沿用 `bcs-cli` 的先例。

设计：[`../06-interfaces.zh-CN.md`](../06-interfaces.zh-CN.md)。

## 影响

- OpenAPI v1 对 bot 调用方的普遍拒绝规则对其他所有端点继续有效。
- 准入测试必须证明 bot 主体在进化作用域之外被拒绝，并且无法跨越 bot 边界。
- 新的公开 CLI（`avn`）需要一个与 `bcs-cli` 相当的覆盖率门禁。

## 备选方案

- **恢复 bot→owner 回退机制。** 已否决：会把所有者的全部权限交给 bot，包括
  晋升。
- **仅使用引擎原生的自编辑工具。** 已否决：按引擎各自实现、未经评审的写入；
  违反引擎中立性和 DR-2。
- **用 MCP server 替代 CLI。** 暂缓：以后可以从同一套 API 生成，供不具备
  `exec` 的引擎使用。
