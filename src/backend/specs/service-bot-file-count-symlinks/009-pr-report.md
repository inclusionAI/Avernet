# PR convergence: service-bot-file-count-symlinks

## Scope
- Repository: GitHub inclusionAI/Avernet (remote github, WRITE access verified).
- Development branch: feat/service-bot-file-count-symlinks.
- Approved result branch: rebase/service-bot-file-count-symlinks-on-REL20260922.
- Base: REL20260922; initial fetched SHA cf20a597b2e058c9baa0d017d0b432a7235f08d3. It will be refreshed before rebase.
- PR: not created yet; prior file-count PR 2380 is merged and is not being updated.
- Planned title: fix(service-bot): count symlink targets with longer scan deadlines
- Description sections: Problem / Solution / Validation / Compatibility and risk.
- Human comment mode: auto. No reply/thread-resolution/merge/deployment authorized or performed.

## Local validation
- Backend: 19709 passed, 43 existing conditional skips, zero failures; coverage 102483/114849 (89.23%), changed production line 1/1 (100%).
- Engine: 2861 passed, 5 CI-matching corp-only deselections, zero skips/failures; coverage 41388/44161 (93.72%), changed lines 266/267 (99.63%, includes colocated tests).
- Backend architecture: 314 passed. Final independent runtime/Engine HTTP contract: 53 passed; Engine focused: 52 passed.
- Secret scanner false positive on a ContextVar handle variable corrected by accurate naming; 17 link tests rerun and secret scan passes. No scanner weakening.
- Fixed nonempty staging tree f93de31cde47209b31010579537135912766146f; full-suite production source matches it. Final commit gates will be recalculated.

## Reviews and CI
Local review/regression reports: 003-review-report.md / 003b-regression-report.md.
Remote automated comments: NOT_CHECKED (PR not created).
Remote CI: NOT_STARTED; local evidence is not remote PASS.
Human comments: NOT_CHECKED.

## Current state
PR NOT_CREATED. Next: commit reviewed changes, refresh/rebase on GitHub REL20260922, push result branch, create and attach PR, inspect actual head checks and review comments.
