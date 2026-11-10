# mine 页序契约：reachability 分组 + created_at 降序

2026-11-10 落地。对象是
`GET /openapi/v1/collaboration/bots/mine`（共用 handler 的
`GET /collaboration/user/bots/mine` 行为随动）；bot-authority 设计
§8.0 的排序语义细化，与该文档同读。

## 决定（用户已确认的事实前提）

1. **`reachability=reachable|unreachable` 过滤参数早已存在**（bot-authority
   #2625 起），语义保持：物理 Bot 且 `status=online` 且（WS 在线 或 HTTP
   Provider 可路由）；显式指定过滤时排除 Human self 行；total 在过滤后、
   分页前。本次不做任何过滤语义变更。
2. 排序前的事实序是 `bot_id` 升序（两 store 的稳定候选序）；契约文档承诺的
   `created_at_desc` 从未在长链路上生效。本次以本契约统一两侧，顺带消除
   实现与已发布契约的不一致。
3. 遗留 `GET /bots/my`（`services/bcs-bot` 的独立分页实现）**不改**。

## 页序契约

排序在**过滤之后、total/分页之前**对全部候选一次性应用（内存稳定排序，
复用同一次 §8.0 runtime 观测；候选已在内存，无新增 DB 往返）：

1. Human self 行恒置顶（rank 0；self 无 reachability 语义）；
2. reachable 物理 Bot 组在 unreachable 组前；
3. 组内 `created_at` 降序；
4. bot_id 升序作为确定性兜底。

显式指定 `reachability` 过滤时组键恒定，排序自动退化为纯 `created_at`
降序；self 行仍按既有行为被过滤排除。

## 实现要点

- `ControlledBotCandidate` 增加 `created_at`（行级 `gmt_create`，毫秒）：
  SQL 三 UNION arm 各 select `({flavor.unix_ts("…gmt_create")}) * 1000`，
  memory 从 control-plane audit 读出；**store 的 `ORDER BY bot_uuid` 不变**
  （owner 行在前的去重次序依赖它）。
- `mine_attempt` 在过滤后插入 `sort_by(mine_order_key)`，key =
  `(组 rank, Reverse(created_at), id)`。
- 排序键只读 §8.0 已采样的事实，drift/composition 语义不变。

## 契约同步

- `api-contracts/v1/openapi/bots.yaml#MyBotsPath`：
  `x-avernet-behavior.ordering` → `[human_self_first, reachable_first,
  created_at_desc, bot_id_asc]`；summary 从过时的 “created by” 改为
  owner|manager 并集；`reachability` 参数与 200 响应描述补齐排序语义。
- `tests/openapi/test_bot_v1_contract.py` 的 x-avernet-behavior 断言随动。
- gateway `configs/schemas/bcn.openapi.json` 重新导出经兼容门禁校验为
  **零新增 breaking**；但发布暂被 avatar_url required 化的既有漂移阻塞
  （属 avatar 工作 stream 的协调发布，非本变更），故本次不更新该 JSON，
  待协调发布时一并落地。

## 验证

- app：`bcs-app-bot --test authority_mine` 新增 4 用例（self 置顶/reachable
  分组在前/组内 created 降序、排序先于分页、显式过滤退化序、创建时间并列按
  id 兜底）；4 个既有 drift/角色漂移用例改为显式 `created_at` 钉页组成
  （经 `MemoryBotRepo::control_plane_audit_state` 新 fixture 面，与
  `authority_state` 同类），消除毫秒注册钟的次序不确定性。
- store conformance（`bot_mine_contract`，SQL+memory 双跑）新增：
  候选携带行级 `created_at`（bot 与 human self 两行均 >0）。
- 本机 `bcs-bot-store::provider_initial_managers` 5 个 sqlite 用例为
  HEAD 已存在的宿主 SQLite 版本问题（与本次无关，已用 stash 仲裁）；MySQL
  方言由 CI 仲裁。
