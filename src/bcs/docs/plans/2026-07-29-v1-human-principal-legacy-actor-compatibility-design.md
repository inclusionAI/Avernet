# V1 Human Principal 与 Legacy Human Actor 兼容设计

## 背景

Gateway 的用户 Principal 表达已经认证的原始用户身份。它携带
`tenant` 和 `subject: AuthenticatedUser`，其中 `subject.id` 是 BCN
现有 `created_by` 使用的用户标识。

BCN 现有群组、关系和 DM 逻辑则把 Human 物化为 `bcs_bots` 中的
`human_<staff_no>` Actor。这个前缀是 BCN 的内部兼容约定，不应要求
Gateway 生成或写入 Human Principal。

本次 V1 API 重构不迁移现有 Human Actor、关系或 DM 数据，也不改变
Legacy API 的业务语义。

## 决策

### Principal 边界

`HumanPrincipal` 只包含 Gateway 提供的原始身份信息：

```text
HumanPrincipal
├── subject.id
├── subject.username
├── subject.display_name?
├── subject.full_name?
├── tenant
└── scopes
```

它不保存或接收 BCN `actor_id`。BCN 在应用层将 Human Principal 投影为
现有内部 Actor ID：

```text
subject.id = "12345"  ->  BCN actor_id = "human_12345"
```

Bot Principal 继续直接使用全局唯一的 `bot_uuid`。

### Human Actor 物化

V1 Human 创建 DM 前继续调用现有 `ensure_human_actor`。调用参数为：

- `staff_no = principal.subject.id`
- `nick_name` 沿用当前 V1 展示名称优先级：
  `display_name -> full_name -> username`

`ensure_human_actor` 的现有幂等语义保持不变：已存在的 Human 行不覆盖
已有名称；参数只影响首次创建。

物化后，V1 把 `human_<subject.id>` 传给现有 `GroupManagementService::create_dm`，
复用 Legacy 的 Human-Bot 授权、关系判断和已有 DM 复用逻辑。

### Identity 与租户

`created_by` 继续保存裸 `subject.id`，不拼接 tenant。当前业务约束认为：
不同租户中相同的 `subject.id` 表示同一个自然人。

tenant 仍保留在 Principal 中作为认证上下文，但本次不参与 Human Actor ID、
`created_by`、关系、群组或 DM 的持久化键。

## 不在本次范围内

- 不增加用户表。
- 不删除或迁移 `bcs_bots` 中的 Human 行。
- 不增加 tenant 数据库列或复合身份键。
- 不修改 `created_by` 的格式和比较规则。
- 不修改 Legacy Route、Human display name 修复或 owner edge 逻辑。
- 不修改已有关系、群组、Session 或 DM 的身份格式。

## 兼容性验证

回归测试必须证明：

1. Human Principal 的序列化不再包含 BCN `actor_id`。
2. `subject.id = "staff-1"` 在 BCN 内部映射为 `human_staff-1`。
3. V1 Human 创建 DM 会创建缺失的 Human Actor 行。
4. Human Actor 首次名称沿用 `display_name -> full_name -> username`。
5. 已存在 Human Actor 的名称不会被 V1 调用覆盖。
6. V1 Human-Bot DM 继续走现有 Group Management 路径并复用相同 Actor ID。
7. `created_by` 继续与裸 `subject.id` 比较。

