# Tasks
- [x] 1. 补充引用发送契约和调试命令回归测试，验证原实现缺失能力。
- [x] 2. 实现共享命令装配、调试 Sender 与平台扩展接线。
- [x] 3. 相关测试、类型和文件规模检查；检查普通对话兼容。
- [x] 4. 本地组装，连接预发完成个人 Bot 真实页面验证并记录。
- [x] 4a. 补充服务 Bot 编辑阶段回归，修正草稿/线上调试选址，保留 verify。
- [ ] 4b. 真实服务 Bot 草稿/线上启用列表差异验收（不得抢占锁或擅改授权）。
- [ ] 4c. 增加公共消息展示扩展点与测试，内部引用卡片本地组装验证、交付状态单独标注。
- [ ] 5. 自审、提交、推送并创建 Avernet → dev PR（2026-11-10 用户已重新授权：复审通过后提交）。

## Release integration (2026-11-11)
- [x] R1. Composer 扩展接线与混合文件发送红绿回归。
- [x] R2. 消息展示 Host、完整输入上下文与文件阶段/取消隔离回归。
- [x] R3. 相关公共测试、类型、lint 与边界检查。
- [ ] R4. 隔离双仓组装、内部实际 Sender 回归和本地前端验证，记录真实缺口。
- [x] R5. 自审与结果汇报；本轮不推送或创建 PR。


## Latest dev follow-up (2026-11-11, authorized)
- [x] D1. Rebase only the feature patch onto current public dev; refresh the enterprise dev baseline.
- [x] D2. Adapt the release integration stage test; add a red/green assertion that personal debug uploads omit stage.
- [x] D3. Reassemble paired sources; run module regressions, type/lint/build and disclose pre-existing full-gate failures.
- [x] D4. Read-only live service draft/online knowledge-selection contrast and file/knowledge chip coexistence; no lock takeover or enablement changes.
- [x] D5. Create both dev-targeted PRs, explicitly declaring the enterprise PR dependency and final gitlink integration boundary.
