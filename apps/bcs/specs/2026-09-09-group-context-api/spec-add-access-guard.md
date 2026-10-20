# Group Context Add 访问校验 — Spec

- **日期:** 2026-10-19
- **状态:** 已确认需求

---

## 概述

Group Context `add` 必须根据 `scope_level` 选择的边界值解析请求作用域，校验 bot 自定义的 roundId，并在创建 context 之前确认当前 actor 是对应资源边界的成员。

## Scope 边界

必填的请求上下文值只由 `scope_level` 决定：

| `scope_level` | 边界值 | 要求 |
|---|---|---|
| `group` | `groupId` | 必填，且必须对应一个已存在的 Group。 |
| `session` | `sessionId` | 必填。`groupId` 不要求必填。 |
| `round` | `roundId` | 必填。`groupId` 不要求必填。 |

因此，`groupId` 不是全局必填字段；只有当 `scope_level=group` 时必须提供。如果 session/round 写入请求显式提供了非空 `groupId`，该值必须与解析出的 Session 所属 parent Group 一致。

当边界值缺失或为空白时，`add` 返回 `invalid_param`，错误信息需要同时指出缺失的参数名和当前 `scope_level`。

## RoundId 格式

BCS 目前没有独立的 round 实体设计。Group Context 通过 bot 自定义的 `roundId` 提供 session 之下更细粒度的作用域。

当 `scope_level=round` 时，`roundId` 必须满足：

- 以 `{sessionId}:` 开头，并且 round 后缀非空；
- 不包含空白字符或控制字符。

当前 sessionId 格式为 `{groupId}:{suffix}`。因此，需要从 `roundId` 的第二个冒号之前的部分反解出 sessionId。如果请求同时显式传入 `sessionId`，它必须与从 `roundId` 解析出的 Session 一致。

## 仅 Add 生效的权限校验

在 `add` 写入 context 之前，actor 必须通过由 `scope_level` 决定的资源级成员校验：

1. **`scope_level=group`**
   actor 必须是目标 Group 的成员。

2. **`scope_level=session`**
   actor 必须是目标 Session 的当前参与者。即使 actor 是 parent Group 的成员，只要它不在当前 Session participants 中，也不能通过 `add` 的资源级校验。

3. **`scope_level=round`**
   先从 `roundId` 解析出 sessionId，然后应用与 `scope_level=session` 相同的 Session 当前成员规则。

Session 成员必须基于当前持久化的 Session participants 判断。parent Group 后续新增的成员不会自动获得已存在 Session 的 `add` 权限；Session 启动后被动态邀请进来的参与者可以通过该校验。

这个资源级校验是在现有 Group Context 写入策略之上新增的校验；通过后，actor 仍然必须满足 `collect_from` 的限制。

## 不变更的操作

资源级成员校验只作用于公开的 `add` 操作。`update`、`list` 和 `get` 继续使用现有 Group Context 权限策略定义的读/写范围控制，不需要增加该成员校验。内部 system 写入也不变。
