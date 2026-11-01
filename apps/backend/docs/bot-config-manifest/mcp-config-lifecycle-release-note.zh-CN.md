# MCP 配置省略语义与生命周期：升级说明

本变更有意修订 Manifest 的既有行为，发布时需要通知依赖 bare MCP 条目清配置的调用方。

| 操作 | 新行为 |
| --- | --- |
| 省略整个 `manifest.mcp` | 供应和配置均不操作，与旧版本相同 |
| 保留 MCP 条目，省略 `config` | 保留当前 Bot override；旧版本会清除它 |
| 显式 `config: {}` | 清除完整 Bot override，恢复继承来源 |
| 显式非空 `config` | 完整替换 Bot override，非字段补丁 |
| 父 MCP 条目被管理范围内的 Replace 移除 | 仍撤销供应并清 override，包括非 Set 的暂存配置 |

升级前若使用下面的条目重置配置，请改成显式空对象：

```yaml
manifest:
  mcp:
    - server_code: mcp.example
      config: {}
```

不自动改写历史 Manifest，也不从设备残留或用户默认值恢复已删除的 override。
Genome 仍是完整快照：快照无 `config` 表示目标无 override，恢复时仍清除后来新增的值。

普通 SkillSet 的成员变更、启停、删除、Default exclusion、Installation 对账与能力补偿
保留 Bot override；Direct 显式卸载、明确配置操作和 Bot 删除仍可清理。当前 Set 供应
继续不在 Manifest 的 Direct-only 管理范围内，显式声明冲突项仍在任何类别写入前拒绝。
保存配置本身不启用能力，重新供应读取当前值并按当前权限、Center 与引擎规则校验。

字段及完整作用域见 [Manifest schema §3.1](manifest-schema.zh-CN.md#31-mcp--mcp-servers)。
