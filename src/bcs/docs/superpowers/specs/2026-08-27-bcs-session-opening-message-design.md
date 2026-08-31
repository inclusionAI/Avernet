# BCS Session Opening Message Design

**Status:** Approved
**Date:** 2026-08-27
**Scope:** BCS Chat and Manager-Worker collaboration sessions

## Problem

BCS already supports a group-level custom `opening_message` for StateMachine
runs. Chat and Manager-Worker groups cannot configure the same experience, so
their first screen cannot present instructions, a card, or a panel before the
user sends a message.

## Confirmed product semantics

1. The rendered opening message is a real public session-history record in
   `bcs_messages`.
2. The opening message is a user-interface message. It is never delivered to a
   Bot and is excluded from Bot history/context replay.
3. Refresh restores the persisted rendered record. The frontend must not
   re-render the current group configuration.

## Behavior

- `opening_message` is accepted for Normal groups using `chat`,
  `manager_worker`, or `state_machine` strategies. Direct-message groups still
  reject it.
- Chat and Manager-Worker render the opening message once when a new session is
  created. Reactivation, reconnect, participant join, and page refresh do not
  create another record.
- StateMachine keeps its existing run-scoped behavior and default opening panel.
- Chat and Manager-Worker have no default opening message when the group omits
  the configuration.
- Session-scoped templates support `bcs.group_id`, `bcs.session_id`,
  `bcs.group_name`, and `bcs.session_name`. `bcs.run_id` remains StateMachine
  only and is rejected for Chat and Manager-Worker configuration.

## Persistence and delivery

- BCS renders from the group configuration at session creation and stores the
  final rendered content as a public assistant message with a stable client
  message ID.
- Persistence succeeds before session creation is reported successful. On a
  persistence failure, BCS completes the newly created session with an error
  and reports the failure.
- After persistence, BCS publishes the same persisted payload to the frontend.
  Realtime delivery is best-effort because history is the recovery path.
- Opening messages do not emit Bot delivery commands or `message.created`
  domain events.

## Acceptance criteria

- Chat and Manager-Worker group create/update APIs accept valid custom opening
  messages and reject `bcs.run_id`.
- Creating a session persists and publishes exactly one rendered opening
  message; reactivation does not duplicate it.
- Human history returns the opening message after refresh while Bot history
  omits it.
- The creation UI offers opening-message configuration for all three Normal
  strategies and applies the strategy-specific template-variable rules.
- Existing StateMachine behavior remains compatible.
