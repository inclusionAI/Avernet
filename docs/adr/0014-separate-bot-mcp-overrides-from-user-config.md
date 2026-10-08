# 单独持久化 Bot MCP 覆盖配置

状态：accepted（当前变更已实现，待合并与部署验证）。

注：本 ADR 中“缺省 Header 与显式空 Header 必须可区分”的有效配置语义，已由
[ADR 0015](0015-merge-user-and-bot-mcp-headers-per-key.md) 修订；存储表分离与
Bot 配置事务边界仍沿用本 ADR。

用户级 MCP 配置是多个 Bot 的继承来源，而 Manifest 只修改目标 Bot。保留 ac_user_mcp_config，新建 Bot 配置表，只存显式覆盖，解析时组合用户配置和 Center 元数据；缺省 Header 与显式空 Header 必须可区分。

没有选择在旧表增加可空 bot_id：现有查询、列表、更新和删除都按用户作用域设计，混入 Bot 行会要求系统性修改旧接口，并需要额外处理默认行的唯一性。分表保留旧存储语义，但仍必须修改各同步入口，让它们逐 Bot 解析有效配置。

同一 Bot/MCP 的安装关系及覆盖配置在一个数据库事务内提交，提交后做 best-effort 运行时投影。该事务不扩展为整个 Manifest 或多个设备的原子提交。

依据：[Q10、Q12 决策](../superpowers/specs/2026-09-21-manifest-mcp-contract-review.md)、[内部方案](../superpowers/specs/2026-09-21-manifest-mcp-internal-design.md)。
