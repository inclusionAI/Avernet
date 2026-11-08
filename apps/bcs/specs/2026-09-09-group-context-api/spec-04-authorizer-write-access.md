# Group Context Authorizer Write Access (collect_from 放开 + get 展示 owners)

- **日期:** 2026-10-20
- **范围:** Group Context 的 add / get,涉及 create/update 的写校验语义
- **状态:** 已实现,远程 CI 验证通过

---

## Summary

当前 `add` 硬性要求 `collect_from` 必须包含创建者自己(`if !command.collect_from.contains_user(&command.actor_id) { return Err(permission_denied) }`),否则 `add` 直接拒绝。但创建者在 `add` 成功后即被写入 `owners=[actor_id]`,本应是默认 owner,拥有最高管理权。现有硬校验与 owner 语义冲突,且是 bot 首次调用 `add` 的高频踩坑点:local 环境下 uuid 是 `20261016_mnfoyzto:199671` 这种格式,机器人容易在 `collect_from` 里漏写自己或用错 id,导致 `permission_denied`。

目标:**owner 权限在 `add` 时即生效,`collect_from` 放开为可选**。创建者默认可写,不再要求显式出现在 `collect_from` 中;`get` 中 owner 额外看到 `owners` 字段,使 owner 有权了解其他 owner。

## Motivation

- **消除 add 的隐式违约:** 创建者本就是 owner,却因 collect_from 不含自己被拒,语义矛盾。
- **降低 bot 首次使用的摩擦:** local 测试环境下 uuid = bot 名,存在格式误解;放开 collect_from 后,`add` 的最小可用命令只需 group/name/scope-level/content,不再需要正确拼写 collect_from。
- **owner 可见性闭环:** owner 有权知道同一 context 还有哪些 owner(add 时可从 `owners` 确认多 owner 是否生效)。

## 用户故事

- 作为创建 bot,我希望不指定 `collect_from` 也能成功 `add` 并默认成为 owner。
- 作为 owner,我希望 `get` 时看到 `owners` 列表,了解共同所有者。
- 作为需要协作的创建者,我仍希望在 `add` 时指定 `collect_from` 让其他 bot 一起维护。

## Acceptance Criteria

- [ ] `add` 不传 `collect_from` → 成功,创建者成为 owner,可正常写。
- [ ] `add` 传 `collect_from` 空列表 → 成功,仅 owner 可写。
- [ ] `add` 传 `collect_from` 含他人 → 他人获得 `W` 写权限。
- [ ] `add` 不再因 `collect_from` 不含创建者而报 `permission_denied`。
- [ ] `collect_from` 为空时不自动写入 actorId,保持空;所有权单靠 `owners` 表达。
- [ ] `get` 中 owner 额外返回 `owners` 字段;非 owner 不返回。
- [ ] `update` 语义不变:非 owner 仍须满足 `collect_from.contains(actor)` + 不改管理字段;owner 例外继续生效。
- [ ] 空 `collect_from`(空列表)在 add 路径合法;update 路径语义保持(非 owner 无写权)。

## 不在范围内

- 不改变 `update` 的写校验逻辑(owner 例外 + collect_from 不变)。
- 不改变 `list` 输出(不展示 owners / 策略字段)。
- 不改变 add 的 Group/Session/Round 成员准入校验(沿用 guard)。
- 不改变 `systemset` 的 owner 默认与沿用行为。

## 已确认设计决策

- `collect_from` 字段从 `GroupContextPolicy`(必填)改为 `Option<GroupContextPolicy>`(可选)。
- 不自动填充 actorId:create 时穿透空列表,所有权仅由 `owners` 表达。
- `GroupContextGetItem` 增加 `owners: Option<GroupContextOwners>`,owner 时 `Some(entry.owners.clone())`,非 owner 为 `None`(与现有 `visible_to` / `collect_from` 的 owner-only 输出一致)。
- update / CLI 的 `--collect-from` 已为 Option,无需改动;仅 add 的 CLI 由必填改可选。

---

## 改动面(供 coding 会话参考)

### 类型层

- `AddGroupContext.collect_from`: `GroupContextPolicy` → `Option<GroupContextPolicy>`
- HTTP `AddRequest.collect_from`: 同改 `Option<GroupContextPolicy>`
- `AddGroupContext` create 内部传 collect_from 空值 / 原值

### 应用层 service.rs

- `add`:删除 `if !command.collect_from.contains_user(&command.actor_id) { Err(permission_denied) }` 硬校验
- `create` 接收 `collect_from: Option<GroupContextPolicy>`,None 映射为空 `GroupContextPolicy`
- `get`: `let owners = is_owner.then(|| entry.owners.clone());`

### 传输层

- CLI `group-context add --collect-from`: `required` → `optional`
- HTTP `AddRequest.collect_from` Option
- CLI add body 构造:collect_from 为 None 时省略该字段

### 校验

- `validate_collect_from` 空列表:add 路径不再报错(空 = 仅 owner 写)
- update 路径保持:非 owner 需 `contains(actor)` 且未改管理字段

### 文档

- `references/group-context.md`: `--collect-from` 说明改为「可选,默认仅创建者(owner)能写,需要其他 bot 一起维护时才填写」
- SKILL.md 场景若提及 collect_from 必填,同步移除「必须把自己加入 collect_from」的强调
- 本 spec 同步为已确认

---

## 依赖的现有类型(全量路径)

- `bcs-service-api::application::v1::AddGroupContext`
- `bcs-http::routes::group_context::AddRequest`
- `bcs-cli::group_context_add_body` / `GroupContextCommands::Add`
- `bcs-app-group-context::service::add`
- `bcs-app-group-context::permissions::actor_can_write`

## 风险与缓解

- **collect_from 必填改可选是放宽**,旧请求(必填时期合法写的)仍满足新规则,不破坏存量。
- **语义从「硬校验」变「owner 例外」**,PR 描述需说明,防止 reviewer 误判为权限漏洞。
- **存量测试依赖「collect_from 必含自己」** 的用例会失效,需逐个校正(新增 add 不传 collect_from 成功、仅 owner 可写、owner 看到 owners 等)。

## Rollout

- 无需数据库迁移(纯类型/逻辑改动)。
- 后端服务与 CLI 同步发布;CLI 旧版仍能调用(collect_from 传了则行为不变),新版不传也兼容服务端。