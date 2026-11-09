# Group Context 名称预定义与会话继承

## 概述

Group 可以维护一份预定义的 Group Context 名称及描述，供会话参与者发现并显式使用。创建 Session 时可以指定源 Session，将其中的上下文带入新会话，减少重复整理。

## 用户故事

- 群管理者可以维护一个群聊的固定名称的group-context，例如问题排查群要有「时间线」「归因」等记录项及其用途。
- Bot 可以查询当前群的预定义名称，在创建会话上下文时可以显式选择其中一项
- 会话创建者可以从同群源会话继承显式指定名称且可读的上下文。

## 范围

本期包括 BCS 后端服务接口及上述 CLI 能力。前端的上下文展示与 slot 编辑留到后续。

## Bot 入口（BCS CLI）

group-context新增一个字段，is_slot boolean类型，记录这条group-context是否是slot，服务端持久化，`list/get` 不返回
后续管理时slot定义变更不影响已经记录的is_slot字段

| 编号 | 功能 | 一期规则 |
|---|---|---|
| B1 | 查询预定义 slot | 列出当前 Group 的 slot 名称和描述，bcs-cli group-context list-slot --group-id 'xxx' |
| B2 | 按 slot 创建/修改 Group Context | 在现有 `group-context add` 中显式选择当前 Group 的 slot，以该名称创建 Session 作用域的上下文，创建 bcs-cli group-context add --scope-level 'session' --session-id 'xx' --name 'xx' --context 'xxx' --slot，如果没有--slot参数，name 不可以是slot定义的名字，更新Group-context时沿用原有is_slot字段|
| B3 | 创建 Session 时继承上下文 | 在现有 `session create` 中指定同群源 Session。不指定名称时，继承全部session级别的、当前bot可读的group-context，否则仅继承指定名称列表部分，每个名称都必须对应可读的当前条目，否则整次创建失败。副本沿用源条目的读写策略， session的创建者成为新owner，并建立独立版本链。 新增参数 --context-from-session-id 'xx' --context-names '["name1","name2"]'，--context-names可省略。单次最多继承 500 条，超过上限时整次创建失败。|

继承生成的 Group Context 保留源条目的 `is_slot` 值。源条目未记录该字段时，副本按 `false` 处理。

## Human 入口（后端管理接口）

管理操作成功后，下一次 list-slot 和 add --slot 使用新定义

| 编号 | 功能 | 一期规则 |
|---|---|---|
| H1 | 查询 slot | 按 Group 列出 slot 名称和描述。 |
| H2 | 新增 slot | 在指定 Group 下新增 slot。名称在群内唯一，描述可空。 |
| H3 | 编辑 slot | 可以修改名称或描述。名称变更后，后续创建使用新名称，已有上下文保留原名和内容。 |
| H4 | 删除 slot | 从 Group 的预定义列表中移除该 slot，已有上下文保留原名和内容。同名重建后，新 slot 可供后续创建时选择。 |

## 权限与审计

已有身份认证、成员准入和条目读写权限继续生效。Slot 管理接口本期要求身份认证，角色授权后续补充。新增时以实际调用者记录 creator 和 modifier，编辑时只更新 modifier。会话继承需遵守源条目的读取权限，并避免通过复制扩大内容的可读范围。
