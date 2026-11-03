# Tasks: Unified stage selections

> 用户已确认整体流程并要求开始实现。旧实现完成记录见 Git 历史；以下为本次新增范围。

## Task 1 [x]: Stage storage and best-effort projection
- **Goal:** 有界版本读取、线上来源标记、原生部署完成后的辅助投影。
- **Done when:** 状态隔离、回滚、未知、CAS不变、失败隔离测试通过。
- **Verification:** 12个真实DB状态用例通过；任务回归及管理/会话相关581个用例通过，3个新HTTP用例修正外部Port绑定后单独通过。

## Task 2 [x]: Existing management and conversation HTTP contract
- **Goal:** 复用两个现有URL，删除独立发布读取URL，保留authorization和个人Bot兼容。
- **Done when:** HTTP/真实DI/会话与管理边界测试通过。
- **Verification:** 独立新worktree完整定向组621 passed（包含真实HTTP/DI、模块边界、永久Protocol契约）。原worktree单文件反复回写旧内容，保留现场并迁移到新worktree，未继续覆盖旧文件。

## Task 3 [x]: Frontend stage propagation and readonly states
- **Goal:** 现有管理和会话界面接入stage-aware选择。
- **Done when:** 预发/线上只读、未知空列表无提示、会话懒加载和分页测试通过。
- **Verification:** assembled前端28 suites / 243 tests、tsc、lint、Bigfish build通过；真实组件+外部catalog fixture浏览器smoke确认未知只有正常空列表。team chat entity和真实知识Owner分离，缺Owner不猜测。

## Task 4 [x]: Verification and independent review
- **Goal:** 更新文档、运行门禁、独立评审并修复。
- **Done when:** 记录实际测试结果和未验证范围，不复用旧绿灯。
- **Verification:** final code/test e0d4f4a468完整Backend CI 28095 passed / 40 skipped / 6 subtests，覆盖率90.72%，变更93.37%，原门禁通过；整体复审发现的空间准入P2已修复，真实HTTP8个新增权限回归和24个状态HTTP用例通过。正常github/dev pre-push通过；保留scanner组合测试43 passed / 1 failed的已复现Release基线问题及真实DB延迟/锁竞争边界。验证文档保留失败证据；仅授权更新原PR，不合并或部署。

## Dev-target correction (2026-11-02)
- [x] Port only the reviewed feature net diff onto dev; keep dev existing scanner/type imports and native restart comment.
- [x] Fresh full Backend CI: 28953 passed / 40 skipped / 6 subtests, changed coverage 93.33%; independent delta review 327 passed, no P2+; public composer 7 passed.
- [x] Rebase onto dev@7cfeb03d without conflicts; tested backend bytes unchanged. Create a new dev-targeted PR; original #3293 is already merged into Release, not reverted by this delivery.

## Groups
- Group A: Tasks 1, 2
- Group B: Task 3
- Group C: Task 4
