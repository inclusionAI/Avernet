# Manifest v1 显式保留默认供应

状态：accepted。补充 [0018](0018-manifest-capability-full-replace-direct-claims.md)。

Manifest 根节点可声明 `apply_policy.skills.preserve_defaults` 和
`apply_policy.mcp.preserve_defaults`，值严格为 boolean。缺失或 false 保持原有
Replace；true 只保护该类目当前有效 Default 供应及其来源。类目缺失不执行 Replace，
策略不能隐式创建类目。未知字段、非 boolean 和能力不支持均在现有校验及创建
preflight 中拒绝；不新增公开接口、schema v2 或 CLI 参数。

`GET .../config-manifest/capabilities` 的 `apply_policy_support` 将支持的类目映射为
`["preserve_defaults"]`。客户端先发现能力；旧服务不支持时拒绝策略，不静默回退。

Skills 保留有效 Default，不复制或冻结包；显式声明与有效 Default runtime name
相同即在该类目首次写入前失败。非默认项仍 Replace。已有 Default exclusion 不变，
被排除的同名能力可以声明成 Local/Direct。

默认 MCP 显式声明只修改 Bot override：非空完整替换，省略或 `{}` 清除；未声明默认
MCP 保留来源但清除旧 Bot override。用户级及平台配置不变。最终 MCP 依赖从最终
全部 Skills 计算，包括保留的 Default；排除 Default 不排除其他来源的依赖或 Direct。

Dry-run、重试均读取当前事实；报告区分保留来源与清理配置。每次受保护的来源写入在
现有 desired-state UoW 内锁定并重新校验 Default 快照，前提变化则停止剩余不安全
写入并如实报告 partial。此快照是内部控制面保护事实，不是全局 revision、锁定窗口
或新公开协议。未知相关 Skill 来源拒绝受影响 Skills 类目，不自动修复历史记录；
MCP-only 不审计无关 Skill 来源。Skills 失败后 MCP 从实际状态重算依赖，读取失败则
在写入前终止 MCP。保持现有逐类目失败边界，不增加整单回滚。
