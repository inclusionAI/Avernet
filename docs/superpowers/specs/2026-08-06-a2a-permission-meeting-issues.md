> **历史归档 / 非实现基线**：本文记录早期讨论方案，可能包含已废弃的 `role`、`edge_id` 下发、`permission_profiles[]` / `rules_grants[]` 拆分、`extra_rules` 等设计。实现请以 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-final-design.md` 和 `docs/superpowers/specs/2026-08-07-bcs-a2a-authz-implementation-spec.md` 为准。

# A2A / Edge Permission 组会问题记录

> 日期：2026-08-06  
> 状态：问题整理稿，待逐项 grill / 收敛  
> 关联文档：
> - `docs/superpowers/specs/edge-permission-schema.md`
> - `docs/superpowers/specs/a2a-permission-and-grant-workflow.md`
> - `docs/superpowers/specs/a2a-permission-and-grant-workflow-briefing.html`

## 0. 当前讨论目标

本文件记录 2026-08-05 左右组会后暴露出的 A2A / Edge Permission 设计问题。当前阶段先不急着定最终方案，而是把问题拆清楚，后续逐个收敛。

核心冲突集中在三件事：

1. **权限定义是否版本钉住**：旧授权是否应该继续按旧 role/rules 鉴权，还是 owner 一改，全网立即按最新定义生效。
2. **授权对象到底是什么**：用户申请的是 role、rules-set、几条 rules，还是一份具体 EdgeGrant。
3. **协作场景是否必须显式两两落边**：群组 / 公开 Bot / 好友关系是否可以产生隐式默认授权。

---

## 1. 问题一：role version 是否保留

### 1.1 原方案

为了方便管理和安全鉴权，role 定义带 `version`：

- owner 更新 role 后，role version 递增。
- EdgeGrant 记录自己批准时绑定的 `role_def_version`。
- A2A 消息里传递 `edge_id + edge_version + role_def_version` 等瘦引用。
- Bot 本地如果发现版本不匹配，就向 BCS 拉取对应版本的授权事实。
- 旧 EdgeGrant 不会自动套用最新 role 定义。

### 1.2 leader 的意见

leader 倾向于：

> 不要 role version。前端改了 role，自然就应该全部更新，所有后续鉴权都按照最新 role 生效。

### 1.3 当前疑问

如果取消 role version，会出现一个正确性问题：

- 前端 owner 修改 role 后，Bot 本地必须尽快同步最新 role。
- 如果 Bot 本地还没同步，却继续按旧 role 鉴权，就会出现误放行或误拒绝。
- 如果 A2A 不做 version 匹配，本地插件如何确认自己拿到的是最新版？

### 1.4 已收敛答案

本轮结论：

> **产品语义上，owner 修改 role 后，已有授权默认跟随最新 role 生效；技术实现上，不能真的取消版本校验，仍必须保留某种 `role_revision` / `role_digest` / `policy_epoch` 来保证 Bot 本地鉴权使用的是 BCS 指定的最新定义。**

换句话说，leader 的“前端改了 role，自然都更新”可以作为产品语义，但不能理解为技术上完全没有版本或 revision。分布式缓存场景下，如果没有任何版本、摘要或 epoch，Bot 本地插件无法证明自己缓存的是最新 role，也无法安全地区分：

```text
本地 role 已经是最新，可以鉴权
vs
本地 role 还是旧的，必须拉取或 deny
```

因此推荐语义是：

```text
owner 修改 role
  → BCS 持久化最新 role，并递增 role_revision / policy_epoch
  → 后续 A2A 消息中，BCS 注入本次要求的最新 revision/digest
  → Bot B 本地发现缓存 revision/digest 不匹配
  → 向 BCS 拉取最新 role 定义
  → 拉取成功后按最新 role 鉴权
  → 拉取失败则 deny
```

这与之前“旧 EdgeGrant 永远钉住旧 role_def_version”的方案不同。新的收敛点是：

- **业务生效语义**：PermissionProfile 修改后，已有授权默认使用最新定义。
- **安全校验机制**：仍需要 revision / digest / epoch，禁止无校验地相信本地缓存。
- **缓存正确性规则**：Bot 本地可以缓存，但必须能证明缓存达到 BCS 在本次 A2A 中声明的版本；证明不了就拉取，拉不到就拒绝。
- **EdgeGrant 责任变化**：EdgeGrant 不再表达“批准时的 role 快照永远有效”，而是表达“from A to B 被批准拥有某个 role 引用”；实际 role 内容取 BCS 当前有效定义。

### 1.5 后续影响

这个答案会影响后续设计：

- `role_def_version` 如果继续存在，不再是“历史快照钉住版本”，而是“当前 role revision 校验字段”。
- A2A `AuthzContext` 应携带 `permission_profile_refs` 的 revision/digest，否则 Bot B 无法判断本地 PermissionProfile 缓存是否过期。
- owner 修改 role 时，不需要逐条重新生成 EdgeGrant；已有 EdgeGrant 自动引用最新 PermissionProfile。
- owner 删除 / 禁用 role 时，依赖该 PermissionProfile 的 EdgeGrant 应该在运行时自然不可用或进入失效态，这一点留到问题三继续收敛。

---

## 2. 问题二：role 这个概念是否好理解

### 2.1 当前模型

当前模型中，role 是一组可复用的权限规则模板：

```text
role = rules_template + 管理属性
```

用户申请权限时，通常是申请 B 暴露的某个 role。

### 2.2 leader 的疑问

role 本身和 rules 在概念上并不一定强绑定。leader 考虑是否应该改名为：

- rules-set
- permission-set
- capability-set
- policy-set

之类的概念。

### 2.3 当前疑问

`rules-set` 听起来更贴近权限规则本身，但产品表达可能更生硬；`role` 更符合用户理解，但会让人误以为它是身份角色，而不是授权模板。

### 2.4 已收敛答案

本轮结论：

> **不要继续把核心概念叫 role。产品侧和内部领域模型都统一改成“权限包 / PermissionProfile”。不要改成 `rules-set`。**

原因：

- `role` 容易让人误解成身份角色，例如 writer / admin / reviewer，像是 A 在 B 那里获得了某种身份。
- 但当前模型真正表达的是：B 暴露出来的一组可申请、可审批、可复用的权限模板。
- `rules-set` 虽然贴近底层实现，但太工程化，只描述“里面是一组 rules”，没有表达申请、审批、展示、管理这些产品语义。
- `PermissionProfile` 更适合作为内部领域名，因为它表达的是“一份权限画像 / 权限配置”，可以包含 rules，也可以包含展示名、描述、风险等级、审批策略等管理元数据。

推荐命名映射：

| 层面 | 推荐叫法 | 说明 |
|---|---|---|
| 产品中文 | 权限包 / 能力包 | 给 owner 和申请者理解，强调“申请后能做什么” |
| 产品英文 | Permission Profile / Capability Profile | 比 role 更准确，比 rules-set 更产品化 |
| 内部领域模型 | `PermissionProfile` | 替代原来的 `RoleDef` |
| 底层字段 | `permission_profile_id` / `permission_profile_revision` | 替代 `role_def_id` / `role_def_version` |
| 内部 rules 字段 | `rules_template` | 保留，表示权限包里的规则模板 |

因此后续文档和 schema 应逐步从：

```text
RoleDef / role_def_id / role_def_version / role_name
```

迁移为：

```text
PermissionProfile / permission_profile_id / permission_profile_revision / permission_profile_name
```

### 2.5 后续影响

这个答案会影响后续设计：

- 申请流程不再表达为“申请角色”，而是“申请权限包”。
- EdgeGrant 不再绑定 `role_def_id`，而是绑定 `permission_profile_id`。
- A2A `AuthzContext.permission_profiles` 中应携带 `permission_profile_id + permission_profile_revision/digest`。
- owner 管理台展示的是“权限包管理”，不是“角色管理”。
- 如果用户只申请几条 rules，不应强行解释成一个 PermissionProfile，而应走独立 `rules grant`；MVP 不支持 `PermissionProfile + extra_rules` 混合授权。

---

## 3. 问题三：EdgeGrant、role、extra rules 的关系

### 3.1 原设想

A 向 B 申请权限时：

```text
申请一个 role + 可选 extra rules
```

B 同意后生成 EdgeGrant：

```text
EdgeGrant = role + extra_rules + 生命周期 + 审批信息
```

### 3.2 mentor 的担忧

mentor 认为，如果一个 EdgeGrant 是 `role + extra_rules`，那么它就不再是一个“固定角色”。后续 owner 修改或删除 role 时：

- 这些已经基于该 role 授出的边如何管理？
- role 被删除后，所有申请到该 role 的边是否应该失效？
- role 修改后，是全部边自动生效，还是要逐条重新批准？

mentor 倾向于把 role 提升为真正的管理对象：

> 如果 owner 删除某个 role，所有依赖这个 role 的授权都应该失效。

### 3.3 leader 的另一个问题

如果 role 是主要管理对象，那么：

- A 只想申请几条权限，不申请一个完整 role，该怎么建模？
- 如果用 `role = null` 或 `adhoc role`，那是不是等于 role 概念被弱化甚至消失？
- 如果 A 想申请某个 role，但需要对几条权限做增补或覆盖，又该怎么实现？

### 3.4 当前冲突

这里有一个核心矛盾：

```text
role 作为“管理对象”
  vs
EdgeGrant 作为“实际授权事实”
```

如果 role 是管理对象，删除 / 修改 role 应该影响所有边。  
如果 EdgeGrant 是授权事实，role 更像申请模板，批准后授权应由 EdgeGrant 自己承载。

### 3.5 已收敛答案

本轮结论：

> **MVP 中 EdgeGrant 只能是两类之一：`permission_profile grant` 或 `rules grant`。不要支持 `PermissionProfile + extra rules` 混合授权。**

推荐模型：

```text
EdgeGrant {
  grant_kind: "permission_profile" | "rules"

  // grant_kind = permission_profile
  permission_profile_id: "..."

  // grant_kind = rules
  rules: [...]
}
```

两类授权语义：

```text
permission_profile grant:
  B 批准 A 拥有某个 PermissionProfile。
  PermissionProfile 更新后，该授权跟随最新 PermissionProfile 生效。
  PermissionProfile 被删除 / 禁用后，该授权在运行时自然不可用或进入失效态。

rules grant:
  B 批准 A 拥有一组独立 rules。
  它不绑定任何 PermissionProfile。
  它用于表达“只申请几条权限”的场景。
```

MVP 明确不支持：

```text
profile grant + extra_rules / delta_rules
```

原因：

- 一旦允许 `PermissionProfile + extra_rules`，这条边就不再是标准权限包授权，而是权限包的变体。
- owner 后续修改 PermissionProfile 时，很难解释 extra rules 是否跟随、覆盖、保留或重新审批。
- owner 删除 PermissionProfile 时，也很难解释 extra rules 是否还有效。
- 审批语义会变模糊：到底批准的是权限包本身，还是批准了一份针对 A 的私有变体。

因此，特殊场景用两个更清晰的路径解决：

1. 如果这是可复用能力组合：owner 新建一个新的 PermissionProfile。
2. 如果只是一次性特殊授权：创建 rules grant，直接存独立 rules。

### 3.6 后续影响

这个答案会影响后续设计：

- EdgeGrant 需要 `grant_kind` 字段。
- `permission_profile_id` 只在 `grant_kind = permission_profile` 时存在。
- `rules` 只在 `grant_kind = rules` 时作为授权事实存在。
- A2A `AuthzContext.permission_profiles` 对 permission_profile grant 携带 `permission_profile_id + permission_profile_revision/digest`。
- A2A `AuthzContext.rules_grants` 对 rules grant 携带 `rules_grant_ref + revision/digest`，不暴露 `edge_id`，也不直接传完整 rules。
- 权限包删除 / 禁用后的授权失效语义需要在 schema 中明确。

---

## 4. 问题四：协作群组是否必须两两建边

### 4.1 场景

一个 bot owner 可能和多个 bot 有好友关系，或者申请过多个 bot 的额外权限包。owner 可以创建协作群组，把这些 bot 拉进同一个协作环境。

但被拉进来的 bot 之间可能没有好友关系，也没有 A→B / B→A 的权限边。

### 4.2 当前理论

之前的理论是：

```text
两两之间交流必须落边，A→B 有边才可鉴权。
```

### 4.3 现实问题

如果群组里有 20 / 50 / 100 个 bot：

- 两两建边数量会爆炸。
- 很多关系只是本次协作临时需要，不应该在群组解散后继续存在。
- 如果为了群组协作申请好友或申请权限包，退出群组后还保留关系，会不符合预期。
- 如果退出群组要撤销所有关系，又引入额外撤销流程和复杂状态管理。

### 4.4 最新 mentor 方案

本轮更新：

> **不要把 default 专门设计成“不落边的虚拟边”，也不要引入 `VirtualGrant` / `ActiveGrantSnapshot` 作为核心概念。系统仍然有边库；BCS 在 A2A 路由时根据边库和当前 context 计算本次应下发的 `permission_profile_refs` 和 `rules_grant_refs`。**

关键变化：

```text
加好友 / connect：
  真实落双向 default 边
  A → B default PermissionProfile
  B → A default PermissionProfile

专门申请权限：
  真实落 A → B permission_profile grant
  或真实落 A → B rules grant

协作群组：
  不为群组成员两两落 default 边
  BCS 查询边库中已有的 A → B grants
  再根据 collaboration context 给本次 A2A 补充 B.default PermissionProfile
```

这里“补 default”只是 BCS 运行时下发本次可用的 default PermissionProfile 引用，不落永久 EdgeGrant，也不需要命名为 VirtualGrant。

### 4.5 A2A 下发对象

A2A 不再传 `edge_id`。BCS 对内可以使用 edge_id 查库、审计、撤销，但对 Bot B 下发的是本次可用的授权引用：

```text
permission_profile_refs:
  - permission_profile_id
  - revision / digest
  - source

rules_grant_refs:
  - rules_grant_ref
  - revision / digest
  - source
```

示例：

```json
{
  "authz_context": {
    "from_id": "A",
    "to_id": "B",
    "env": "prod",
    "context": {
      "type": "collaboration",
      "group_id": "G1"
    },
    "permission_profiles": [
      {
        "permission_profile_id": "profile_B_default",
        "revision": 8,
        "digest": "sha256:...",
        "source": "collaboration_default"
      },
      {
        "permission_profile_id": "profile_B_writer",
        "revision": 5,
        "digest": "sha256:...",
        "source": "edge_grant"
      }
    ],
    "rules_grants": [
      {
        "rules_grant_ref": "rg_opaque_abc",
        "revision": 3,
        "digest": "sha256:...",
        "source": "edge_grant"
      }
    ],
    "issued_at": 1785900000000,
    "expires_at": 1785900300000
  }
}
```

说明：

- `permission_profiles` 表示本次 A→B 可用的权限包引用，可能来自边库，也可能来自 context 补充的 default。
- `rules_grants` 表示本次 A→B 可用的独立 rules 授权引用。
- `rules_grant_ref` 是 BCS 生成的 opaque ref，不是对外暴露的 `edge_id`。
- A2A 不直接携带完整 rules，避免消息臃肿和规则细节暴露。

### 4.6 Bot B 本地缓存和拉取

Bot B 本地插件不再缓存 `EdgeGrant` 或 `ActiveGrantSnapshot`，而是缓存两类可鉴权材料：

```text
permission_profile_cache
rules_grant_cache
```

#### PermissionProfile cache

```json
{
  "permission_profile_id": "profile_B_default",
  "revision": 8,
  "digest": "sha256:...",
  "rules_template": [
    {
      "tool": "chat",
      "specifier": "*",
      "decision": "allow"
    }
  ]
}
```

#### RulesGrant cache

```json
{
  "rules_grant_ref": "rg_opaque_abc",
  "revision": 3,
  "digest": "sha256:...",
  "from_id": "A",
  "to_id": "B",
  "env": "prod",
  "rules": [
    {
      "tool": "LarkDoc",
      "specifier": "doc:123",
      "decision": "allow"
    }
  ],
  "expires_at": null
}
```

Bot B 收到 A2A 后：

```text
B 收到 A2A message
  → 遍历 authz_context.permission_profiles
  → 本地按 permission_profile_id + revision/digest 校验缓存
  → 缓存缺失 / digest 不匹配则向 BCS 拉取 PermissionProfile

  → 遍历 authz_context.rules_grants
  → 本地按 rules_grant_ref + revision/digest 校验缓存
  → 缓存缺失 / digest 不匹配则向 BCS 拉取 RulesGrant

  → 拉取失败 / digest 不匹配 / BCS 判定已失效
      → deny

  → 拉取成功
      → PermissionProfile.rules_template + RulesGrant.rules 拼成本次权限集
      → before_tool_call 鉴权
```

推荐接口：

```text
GET /authz/permission-profiles/{permission_profile_id}?revision=...
POST /authz/rules-grants/resolve
```

`rules-grants/resolve` 输入 `rules_grant_ref + revision/digest + from_id + to_id + env`，BCS 内部再映射回真实边库记录并校验状态。这样既不暴露 `edge_id`，也不把完整 rules 塞进 A2A。

### 4.7 群组授权边界

MVP 建议对 collaboration context 补 default 加强约束：

- 只补目标 Bot 的 default PermissionProfile。
- 不补高阶 PermissionProfile。
- 不跨 env 使用。
- group 解散、成员退出、session 过期后，不再补 default。
- 不为群组成员两两落永久 default EdgeGrant。
- 如果群组内某个 bot 需要另一个 bot 的高阶权限，仍需要显式申请并落 EdgeGrant。

---

## 5. 问题五：公开 Bot / 申请 Bot 怎么进入授权模型

### 5.1 产品场景

广场上的 Bot 分为：

- **公开 Bot**：用户点开就能直接聊天。
- **申请 Bot**：需要申请并审批后才能聊天。

### 5.2 最新 mentor 方案

本轮更新：

> **公开 Bot 不为所有人落 EdgeGrant，但 BCS 在公开聊天 context 下会额外下发目标 Bot 的 default PermissionProfile；申请 Bot 通过 connect 审批后，真实落双向 default 边。**

因此三类场景分别是：

```text
公开 Bot：
  A 与公开 Bot B 聊天
  BCS 先查 A → B 边库，取已批准的 permission_profile grants / rules grants
  BCS 再根据 public_bot context 额外补 B.default PermissionProfile
  不为所有人创建永久 EdgeGrant

申请 Bot / 加好友：
  A 与 B connect 审批通过
  边库真实落：A → B default
  边库真实落：B → A default
  后续聊天时，default 来自边库命中

高阶权限包：
  必须单独申请
  审批通过后生成 A → B permission_profile grant 或 rules grant
```

### 5.3 产品语义

产品上可以这样解释：

```text
“公开 Bot 点开直接聊天”
  = public context 允许 BCS 本次下发 B.default PermissionProfile
  ≠ 对所有用户预先创建永久边
  ≠ 用户拥有任何高阶权限包

“申请 Bot 审批通过后可聊天”
  = connect 审批通过后真实落双向 default 边
  = 后续 default 来自边库

“申请权限包”
  = 申请高阶 PermissionProfile 或独立 rules grant
  = 通过后生成显式 EdgeGrant
```

### 5.4 后续影响

这个答案会影响后续设计：

- Bot 必须有 default PermissionProfile。
- Connect / 加好友审批通过时，应落双向 default EdgeGrant。
- 公开 Bot 不为所有 caller 落永久 EdgeGrant，而由 BCS 在 public context 下补 default。
- 协作群组也不两两落 default EdgeGrant，而由 BCS 在 collaboration context 下补 default。
- default 聊天权限和高阶权限包授权必须分离。
- A2A 运行时下发 `permission_profile_refs + rules_grant_refs`，不下发 `edge_id`。

---

## 6. 问题六：产品端需要支持哪些功能

### 6.1 暂定产品功能拆分

本轮暂定：

> **产品上必须把“可聊天”和“高阶权限包”分开。Connect 审批通过会落双向 default 边；公开聊天和群组协作由 BCS 根据 context 临时补 default；高阶 PermissionProfile / rules grant 必须单独申请。**

产品能力建议分成五块：

```text
1. Bot 公开性配置
2. Connect / 连接申请
3. PermissionProfile / 权限包管理
4. 权限包 / rules grant 申请与审批
5. 协作群组权限解释与排查
```

#### Bot 详情页 / 广场

- 显示 Bot 是公开 Bot 还是申请 Bot。
- 公开 Bot：支持直接聊天。
- 申请 Bot：支持发起连接申请。
- 单独提供“申请权限包”入口。
- 明确“可聊天”只代表 default 权限，不代表拥有高阶权限包。

#### Connect / 连接申请

- 发起 Connect / 加好友申请。
- 审批 Connect 申请。
- 审批通过后真实落双向 default 边：`A→B default` 与 `B→A default`。
- 展示申请状态：pending / approved / rejected / revoked。
- 区分“可聊天关系”和“高阶权限授权”。

#### Owner 管理台

- 设置 Bot 是否公开可聊。
- 配置 default PermissionProfile。
- 创建 / 编辑 / 禁用 PermissionProfile。
- 查看哪些 actor 申请了哪些权限包或 rules grant。
- 审批连接申请。
- 审批权限包 / rules grant 申请。
- 修改 PermissionProfile 时展示影响范围：已有 permission_profile grant 会跟随最新定义生效。

#### 权限包 / rules grant 申请与审批

- 支持申请某个 PermissionProfile。
- 支持审批通过后生成显式 permission_profile EdgeGrant。
- 支持申请独立 rules grant 的能力可保留，但 MVP 是否开放给产品界面待定。
- 不支持 `PermissionProfile + extra_rules` 混合申请。

#### 群组协作

- 展示“群组内默认可用对方 default 能力”。
- 说明 default 是 BCS 根据 collaboration context 本次补充，不会为群组成员两两创建永久边。
- 高阶权限仍需单独申请。
- 群组解散、成员退出、session 过期后，BCS 不再补 default。
- 群组协作不应制造大量永久 EdgeGrant。

#### 审计排查

- 展示某次 A2A 消息下发了哪些 `permission_profile_refs` 和 `rules_grant_refs`。
- 展示来源：edge_grant / connect_default / public_default / collaboration_default。
- 对内部审计，可以继续追溯到 BCS 边库里的 edge_id，但 edge_id 不进入 A2A 消息。
- 展示一次 tool 鉴权为什么 allow / deny。
- 区分 default 权限和高阶 PermissionProfile 权限。

### 6.2 产品文案需要避免的误解

产品表达上要避免三类误解：

```text
我能聊天 = 我有所有权限
我加好友 = 我拿到了高阶权限包
我进群了 = 我永久获得了其他 bot 的能力
```

推荐统一解释：

```text
可聊天：
  只代表 default 权限。

Connect / 加好友：
  审批通过后会建立双方 default 可聊关系。

公开 Bot：
  点开可聊，因为公开场景下 BCS 会补 default。

群组协作：
  默认只开放 default 权限，临时有效，不两两落永久边。

权限包 / rules grant：
  代表更高阶能力，需要单独申请。
```

---

## 7. 当前收敛后的统一模型

### 7.1 核心事实

当前统一模型是：

```text
持久事实：
  EdgeGrant 边库
  PermissionProfile 权限包
  RulesGrant 独立 rules 授权

运行时计算：
  BCS 根据边库 + env/context 计算本次 A2A 应下发什么

A2A 下发：
  permission_profile_refs
  rules_grant_refs

Bot 本地缓存：
  permission_profile_cache
  rules_grant_cache
```

### 7.2 什么时候落边

只在两类场景落边：

```text
1. Connect / 加好友审批通过：
   A → B default
   B → A default

2. 专门申请权限审批通过：
   A → B permission_profile grant
   或 A → B rules grant
```

不落边的场景：

```text
公开 Bot 点开聊天：
  BCS 根据 public_bot context 补 B.default

协作群组：
  BCS 根据 collaboration context 补目标 Bot default
```

### 7.3 A2A 鉴权流程

```text
A sends message to B
  → BCS 查 A → B 边库中满足 from/to/env/status/expires_at 的 grants
  → BCS 提取命中的 PermissionProfile refs / RulesGrant refs
  → BCS 根据当前 context 判断是否额外补 B.default
      public bot context: 补 B.default
      collaboration context: 补 B.default
      connect context: 不需要特殊补，因为 connect 已经落 default 边
  → BCS 把 permission_profile_refs + rules_grant_refs 放进 A2A AuthzContext
  → Bot B 本地按 revision/digest 校验缓存
  → cache miss / mismatch 则向 BCS 拉取 PermissionProfile 或 RulesGrant
  → 拉取失败则 deny
  → 拉取成功后拼出权限集并 tool 鉴权
```

### 7.4 当前仍待进一步细化的问题

后续还需要继续细化，但不影响当前主线的点：

- `rules_grant_ref` 的生成、有效期、是否绑定 task/message。  
- `permission_profile_digest` 和 `rules_grant_digest` 的 canonical 计算字段。  
- BCS 补 default 时，public context 和 collaboration context 的优先级、去重逻辑。  
- Bot 本地 cache LRU / TTL / invalidation 细节。  
- 权限包禁用 / 删除后，已有 permission_profile grant 的运行时表现。  

---
