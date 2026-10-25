# Tasks

- [x] Remove feature-owned membership enhancements and audit; retain one current selection table.
- [x] Preserve the existing MCP Header contract; catalog uses business Bot plus user.
- [x] Verify service-Bot draft-only edits without knowledge version linkage.
- [x] Run focused, HTTP, contract, startup and adjacent architecture regression checks.

## Local validation

Adjacent suite: 1128 passed. Current selection, contracts, HTTP and two-phase boot:
42 passed (overlapping; do not sum). The latter also verifies disabling does not
call the remote catalog. Existing unused textwrap lint debt is reproduced from
fixed HEAD; changed-file formatting passes. No commit, push, PR or deployment SQL
was executed. This is controlled local evidence, not production acceptance.


## Public PR delivery (2026-10-23)

The user has authorized this public enablement-only PR. No authorization
management, private adapter or MCP transport change is included.

- [~] Fix the three review findings: moved-space recovery, draft query indexes,
      and disabled-row growth / current-selection indexes.
- [ ] Exercise the real consumer in catalog contracts and correct stale UUID docs.
- [ ] Re-run focused/adjacent tests and independent review on the refreshed base.
- [ ] Push normally and open the PR against dev with the repository template.

The earlier "no commit/push/PR" text records the previous validation snapshot;
DDL execution and deployment remain unauthorized.

## PR #3087 review repair (2026-10-25, local only)

- [x] 刷新远端 Head/评审与本地状态；在隔离 worktree 修复，不修改主 checkout。
- [x] 双租户 default draft 回归先红后绿；保留可证明归属的历史 source PK，孤儿 fail closed。
- [x] 通过原生 add_editor 创建 Personal Editor 的读写/关闭回归；保留 Team 撤权与事务内复查。
- [x] 增加 3 个 live HTTP/DB stories，接入统一 manifest、完整 Core/Router/Plugin 分母与实测阈值。
- [x] 真实独立 Backend 进程执行 stories；只替代外部 catalog，不直接调用业务服务制造覆盖。
- [x] 执行 focused 和 all-module Singlebox 入口，保存环境阻塞证据；artifact verifier 如实失败。
- [ ] 网络/依赖可用环境中，完整跑通 focused + all-module shared stack 和 canonical artifact verifier。
- [ ] 用户决定是否提交、推送及后续 PR 更新；本轮均未执行。

详见 `review-repair-validation.md`。单独 Backend、单元/架构测试的通过不代表统一全栈门禁通过。
