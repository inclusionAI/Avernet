---
status: approved
owners: [bcs]
---

# Preserve delivery failure feedback after queue admission

The user approved repairing queue failure notifications on 2026-10-09 and
specified backend-only changes, with the same error presentation as the
synchronous path.

## Problem

Queue admission returns before Provider delivery. A later rejection commits a
failed delivery but bypasses the synchronous system-chat error notice.
`message.delivery.updated` alone is not rendered by the existing workbench.

## Contract

- After a Group or System Send delivery fails, publish the existing `chat`
  system-message envelope, using the same session, visibility and wording as
  synchronous delivery. Generic failures display “消息投递失败，请稍后重试。”
- Group failures retain the existing availability distinction: genuinely
  unavailable targets display “Bot <names> 已离线”. Notifications use the
  existing GenericNotification producer and its history/delivery policy.
- System-context failures publish the same content-free public notice as the
  synchronous dispatcher. Private initialization text and raw transport errors
  must not appear in that notice.
- Aggregate failures through the existing source-message notification batch.
  Track attempted failure notices per delivery ID, so a target failing after
  an earlier batch is still reported without repeating previously reported
  targets. Other lifecycle hints retain their existing per-status policy.
  Bot terminal errors already projected as chat errors do not add another
  delivery failure notice. Inject, successful and cancelled rows do not emit
  delivery failure notices. Direct A2A and Task terminal reconciliation retain
  their existing paths.
- The old system-chat envelope builder is shared by immediate and queued
  delivery. No frontend, HTTP DTO, Provider Plugin API or schema change is needed.
- Notices remain best effort, matching existing frontend and IM delivery.
  Durable delivery state is authoritative; no outbox, replay or automatic
  resend is introduced. Workbench failures are logged and do not suppress IM
  hints.

## Access cost

No successful transition performs additional reads. At most one existing
notification batch is attempted per 100 ms tick. A failure batch performs one
Group lookup (the production store uses its existing Group cache). Group
notices additionally read one canonical source message by session/message ID
(one indexed MySQL query), resolve failed targets through existing registry
and availability services, and use the existing notification persistence and
Inject path. The second Group lookup in the notification service uses the same
cache. System notices do not read or persist another message. No queue scan,
new polling loop, per-message background task or cache is added. The existing
two-second notification attempt timeout and no-replay policy are retained.

## Validation

Backend integration tests drive committed delivery transitions and the actual
notification subscriber. They cover Group/System failures across Chat,
ManagerWorker and StateMachine strategies, public/private source content,
multi-target aggregation, failures in separate ticks (offline first and
retryable first), and suppression for Bot errors, success and Inject.
Existing synchronous Provider failure tests and queued conformance tests remain
applicable. The repair preserves the existing wire contract.

Executed `cargo test -p bcs-message-flow -p bcs-system-message -p bcs-protocol
--offline`: 727 tests passed. `git diff --check` passed; every modified or added
Rust source file is below 1,000 lines. Frontend has no changes.

The optional full architecture runner reported existing dependency-script,
import, trait-naming and conformance-entry failures; it was stopped during the
conformance inventory, so workspace test discovery did not complete. None of
the reported violations reference this repair's new modules. The baseline
comparison was skipped because its configured `origin/refactor_arch_bcs` ref
cannot be resolved. No architecture rule or baseline was changed.
