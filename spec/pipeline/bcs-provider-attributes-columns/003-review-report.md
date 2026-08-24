# BCS Provider Bot 属性物理列修复：代码评审报告

## 结论

PASS。

## 核验

| 检查项 | 结论 | 证据 |
|---|---|---|
| 读取来源 | 通过 | 所有构造 `BotControlPlaneRecord` 的持久化查询均选择三项物理列，行映射不再读取 `BotInfo` 对应字段。 |
| 更新边界 | 通过 | `patch_control_plane` 仅在字段存在时对相应物理列赋值；描述字段才更新 `bot_info`。 |
| JSON 语义 | 通过 | `friend_ext` 使用参数化 JSON 写入，保持整体替换及 `{}` 清空。 |
| 参数安全 | 通过 | SQL 数据均通过 `?` 参数传入；动态 SQL 只由固定列名/表达式组成。 |
| 兼容范围 | 通过 | 未修改 Provider 路由、鉴权、应用服务、公开接口或 DDL。 |
| 回归证据 | 通过 | conformance 测试断言物理列写入，且三项 `bot_info` JSON path 都为 NULL。 |

## 已知边界

本修复不回填既有 `bot_info` 中的旧属性 key；目标表的物理列是新的唯一读写来源。
