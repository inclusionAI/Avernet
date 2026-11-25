# Bot 好友名单（公开设置）— 增量实现 Spec（2026-11-25）

## 背景与归属

- 需求：Dima `2026112400120934898`《【公开设置】Bot 好友名单：Owner 可查看并管理 Bot 好友》，PRD v1.3。
- 归属：Avernet `apps/frontend-nextgen`「公开设置」弹窗（`BotGeneralConfigDialog` / `CollaborationPrivacy`）；PRD「形态：双仓（Avernet frontend-nextgen，通用功能归属）」。
- 本期范围：公开设置内「好友名单」小节（摘要行 + 数量分计）+ 「管理好友」右侧抽屉（好友用户 / 好友 Bot 两 Tab、当前 Tab 搜索、移除二次确认）。

## 非本期（PRD §6 明确排除）

- 好友名单查询接口合同（分页/排序/搜索的服务端保证）；
- Owner 移除的正式接口合同（关系主体 actor 语义）；
- 移除通知的正式通知服务。
- 按 PRD §8 Demo 口径，本期移除为前端本地即时生效，不臆造后端写接口（避免以登录人为主体的 DELETE 误删本人好友边）。

## 改动清单

- `src/domain/collaborationPrivacy/friendRoster.ts`：纯领域逻辑（分计、按 Tab 过滤、关键词搜索、默认 Tab、DTO→视图映射）。
- `src/hooks/useBotFriendRoster.ts`：以 `listFriendConnections({actor_type:'bot', actor_id, target_type})` 拉取好友用户 / 好友 Bot 两类；移除为 Demo 本地生效。
- `src/components/CollaborationPrivacy/FriendRosterSection/`：`FriendRosterSection`（摘要行）、`FriendRosterDrawer`（右侧抽屉 + Tab + 搜索 + 移除确认）。
- `BotGeneralConfigDialogBody.tsx`：在「好友审批策略」小节下方按 Owner gate 渲染 `FriendRosterSection`。

## 验收映射

| AC | 状态 |
| --- | --- |
| AC-01 打开公开设置可见好友名单摘要行 | 实现（摘要行数量分计） |
| AC-02 管理抽屉两 Tab + 数量 + 默认首个有内容 Tab | 实现 |
| AC-03 无好友空态 + 入口保留 | 实现（「暂无好友」） |
| AC-07 非 Owner 不出现小节与入口 | 实现（`bot.ownerId === userId` gate） |
| AC-08 免审批加入好友同样在名单 | 实现（同一 listFriendConnections 数据源，无审批差异） |
| AC-09 关闭添加开关后存量好友仍可见 | 实现（名单读取不依赖开关） |
| AC-04 移除二次确认 + 行消失 | 确认 UI 实现；移除本地生效（Demo 口径，正式写接口待 §6） |
| AC-05 / 06 / 10 / 11 | 依赖 §6 正式移除接口 + 通知服务，本期未覆盖 |

## 验证

- 已执行：`node scripts/check-open-core.cjs` → Open Core artifact checks passed。
- 未执行：`npm run typecheck` / `npm run lint` / `npm test`（本 worktree 无 `node_modules`，未执行 `npm ci`）；由 CI（`npm ci → check:open-core → npm run ci`）覆盖。
- 待 e2e 核对：`targetBot.id` 作为好友连接 `actor_id` 的格式（是否含 `:port`）。

## 风险

- 移除为 Demo 本地生效，不做后端误写；正式删除 + 通知按 §6 接入。
- 名单读数据依赖 Bot 维度 actor_id 语义，需 e2e 校验。