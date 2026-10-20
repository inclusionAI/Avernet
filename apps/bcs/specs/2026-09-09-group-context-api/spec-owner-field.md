# Group Context Owners

- **日期:** 2026-10-19
- **范围:** Group Context 的 add / update / list / get，以及内部 systemset 的 owner 语义
- **状态:** 需求已确认；可先实现持久化多值 `owners`，creator 的 owner/管理员运行时能力后续接入

---

## Summary

Group Context 新增一个多值 `owners` 字段。`owners` 是一个 JSON object：

```json
{
  "owners": {
    "userIds": ["<owner-uuid>"]
  }
}
```

`userIds` 是既有 policy JSON 的字段名；这里的值统一表示 Group Context 的 owner actor UUID。该结构从一开始就允许多个 owner，避免后续从单值 owner 扩展时再做一次破坏性模型调整。

当前实现的 `creator` 表示该版本行的写入者；本阶段 owner 授权只使用当前 active row 的 persisted `owners`，不依赖 `creator` 语义。`add` 时服务端默认写入：

```json
{
  "owners": {
    "userIds": ["<creator-actor-id>"]
  }
}
```

调用方不能在 `add` 请求中传入或覆盖 owner。

## Motivation

现有 `R/W/WR` 只能表达读写范围，无法表达 Context 的最高管理权。业务上希望 creator bot 及其管理关系中的 owner/管理员都能维护 Context；但“查询 creator bot 及其 owner/管理员”的现有能力尚未 merge。因此本需求先持久化一个可扩展的多 owner 字段，把默认 owner 固定为 creator，同时不在 `owners` 中持久化管理员列表。

## 用户故事

- 作为 Context creator bot，我希望创建后默认拥有 owner 权限。
- 作为被显式加入 `owners` 的 actor，我希望拥有同等最高管理权。
- 作为 creator bot 的 owner/管理员，我希望在运行时被判定为 owner 权限，而不需要把我的身份写入每条 Context。
- 作为非 owner 的普通写入者，我希望仍能维护 `content`、`valid_from`、`valid_to`，但不能修改 `visible_to`、`collect_from` 或 `owners`。

## Acceptance Criteria

- [x] `owners` 是多值字段，使用 JSON object 表达，且 `userIds` 中可以包含多个 owner actor UUID。
- [x] 每条 Context 的 `owners.userIds` 非空。
- [x] `add` 请求不接收 `owners` 输入；服务端默认写入 creator actor id。
- [x] 首次创建的 Context 版本中，`owners.userIds` 首个值等于 `creator`。
- [x] `owner` 权限隐含 `W` 和 `R`，`list` 对 owner 返回 `WRO`。
- [x] owner 可以修改 `content`、`valid_from`、`valid_to`、`visible_to`、`collect_from`、`owners`。
- [x] 非 owner 的 `W` 权限只能修改 `content`、`valid_from`、`valid_to`。
- [x] 非 owner 的 `W` 权限尝试修改 `visible_to`、`collect_from` 或 `owners` 时返回 `permission_denied`。
- [x] owner 判断优先读取当前 active row 的已持久化 `owners`，不使用其他历史版本作为当前授权依据。
- [x] update 授权顺序明确：先判断 actor 是否 owner；owner 不要求出现在 `collect_from`；非 owner 再判断 `collect_from`。
- [x] update 授权使用请求前 active row 的 owners；请求中的新 owners 不参与本次授权，非 owner 不能通过请求把自己加入 owners 来授权。
- [x] owner 不在 `collect_from` 时，仍可以修改 `content` 以及 `visible_to`、`collect_from`、`owners` 等管理字段。
- [x] owner 更新 `owners` 是所有权移交：新列表随新 active 行立即生效，加入和移除同时生效，没有两阶段确认。
- [x] 被移出 `owners` 的旧 owner 立即失去 owner 权限，只能依据当前 `visible_to` / `collect_from` 取得普通读写能力。
- [x] `get` 中 owner 可以额外看到 `visible_to` 和 `collect_from`；非 owner 不能看到这些管理字段。
- [x] 内部 `systemset` 保持系统权限通道语义，不执行 owner 或 `collect_from` 授权判断；首建默认 owner 为 `system`，更新既有 Context 时沿用旧 owners。

## 不在范围内

- 不把单值 `owner` 作为正式 API 或存储模型。
- 不把 creator bot 的 owner/管理员自动展开写入 `owners`。
- 不在 Group Context 中新增独立的 bot 管理员体系。
- 不设计 ownership 两阶段确认或撤销机制。
- 不改变 Group Context `add` 现有的 Group/Session/Round 成员校验。

## 已确认

- 负责“查询群主 bot 及其 owner/管理员”的同事确认该功能尚未 merge。
- 因此本阶段不尝试调用这类未 merge 的接口，也不自行替代实现它。
- `owners` 字段先按多值结构落地，为后续运行时管理员权限接入预留稳定模型。
