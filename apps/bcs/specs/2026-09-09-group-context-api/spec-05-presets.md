# Group Context 名称预定义

## 概述

Group 可以维护一份预定义的 Group Context 名称及描述，供会话参与者发现并显式使用。

## 用户故事

- 群管理者可以维护一个群聊的固定名称的group-context，例如问题排查群要有「时间线」「归因」等记录项及其用途。
- Bot 可以查询当前群的预定义名称，在创建会话上下文时可以显式选择其中一项

## 范围

本期包括 BCS 后端服务接口及上述 CLI 能力。前端的上下文展示与 preset 编辑留到后续。Session 上下文继承随 Fork 功能另行实现。

## Bot 入口（BCS CLI）

服务端持久化布尔字段 `created_from_preset`，记录上下文是否按 preset 创建。`list/get` 不返回该字段。修改或删除 preset 不影响已有上下文的字段值。

| 编号 | 功能 | 一期规则 |
|---|---|---|
| B1 | 查询预定义 preset | 列出当前 Group 的 preset 名称和描述，bcs-cli group-context list-preset --group-id 'xxx'。群成员 Bot 可直接查询，仅参与 Session 的 Bot 必须提供 --session-id，服务端校验同群及当前 Session 成员身份。 |
| B2 | 按 preset 创建/修改 Group Context | 在现有 `group-context add` 中显式选择当前 Group 的 preset，以该名称创建 Session 作用域的上下文，创建 bcs-cli group-context add --scope-level 'session' --session-id 'xx' --name 'xx' --context 'xxx' --preset，如果没有--preset参数，name 不可以是preset定义的名字，更新Group-context时沿用原有created_from_preset字段|


## Human 入口（后端管理接口）

管理操作成功后，下一次 list-preset 和 add --preset 使用新定义

| 编号 | 功能 | 一期规则 |
|---|---|---|
| H1 | 查询 preset | 按 Group 列出 preset 名称和描述。 |
| H2 | 新增 preset | 在指定 Group 下新增 preset。名称区分大小写，在群内唯一，描述可空。 |
| H3 | 编辑 preset | 可以修改名称或描述。名称变更后，后续创建使用新名称，已有上下文保留原名和内容。 |
| H4 | 删除 preset | 从 Group 的预定义列表中移除该 preset，已有上下文保留原名和内容。同名重建后，新 preset 可供后续创建时选择。 |

## 权限与审计

已有身份认证、成员准入和条目读写权限继续生效。Preset 的增删改仅向群主 Bot（`driver_bot`）的 Owner 或 Admin（Bot Authority 中的 Manager）开放。新增时以实际调用者记录 creator 和 modifier，编辑时只更新 modifier。

`list-preset` 本期以方便合法参与者发现 Preset 为目标，沿用现有 Group 成员读取方式。群成员被移除后，其他 BCS 实例可能仍持有旧的 Group 成员缓存，并在缓存刷新前继续放行 Preset 查询。本期暂不要求跨实例即时撤权，也不为此增加强一致授权读取。常规成员准入仍按 B1 执行。
