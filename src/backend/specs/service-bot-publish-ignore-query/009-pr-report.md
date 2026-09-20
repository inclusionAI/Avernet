# PR 收敛报告：service-bot-publish-ignore-query

## 范围
- 日期：2026-09-20。
- Worktree：service-bot-publish-ignore-ops-rel20260917；源码远端 github = inclusionAI/Avernet，已验证 WRITE 权限。
- Head / base：rebase/service-bot-ops-on-dev / dev。
- dev 已 fetch，SHA：8358a14e0987349f562c0ac20f4a9300be737f78。
- 本次原始提交：7726218af，包含查询、取消独立签名、原地重启与文档。
- 旧 PR #2254 已合并至 REL20260917；dev 的既有 ignore 实现与旧 topic 的相应实现一致。本次仅重放旧 HEAD 5cfd442f4 之后的新提交，不重复引入旧 PR 提交及 release backport。
- 人工意见模式：auto。

## 当前状态
- PR：NOT_CREATED；title/body 待验证后根据最终 diff 拟定。
- Rebase：已完成，用户同意保留双方字段后解决两处冲突；重放提交 d6cd3261b，未改变 dev 的 storage_type/env 行为。
- 本地检查：Backend/Engine python_sast_local 阻断规则通过。凭据扫描发现两个非秘密测试占位值；已换成明确 test-token，保留日志不泄漏断言，不降低扫描规则。
- ACI/CI：本地 rebase 后两模块全量测试执行中。2026-09-18 原分支测试结果是历史证据，不代表 rebase 后通过。
- 尚未推送、创建 PR 或部署；daas 启动脚本在另一仓库，不包含在 Avernet 源码 PR。
