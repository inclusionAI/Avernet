# Teclaw Restart Is Not Supported

## Summary
Restarting a teclaw bot currently destroys its container and then fails, leaving
the bot `FAILED` with a stale binding pointing at a container that no longer
exists. Restart is not a recoverable operation for teclaw containers, so this
change makes it an explicit, inert rejection: a teclaw restart request changes
nothing and returns a clear "not supported" response.

## Motivation
`BotService.restart_bot` has two paths. BaaS-provider bots restart **in place**
(BaaS `/update`, binding and container preserved). Everything else falls through
to `stop_bot` + `start_bot` — release the device, then allocate a new one.

Teclaw bindings take the second path, and both halves misbehave:

- `stop_bot` → `release_device` routes by the binding's `device_provider`.
  `"teclaw"` is not a registered provider, so the router silently falls back to
  the BaaS device service, which destroys the container via `destroy_bot`.
- `start_bot` → `apply_device(device_provider="teclaw")` is then refused
  outright (`device_provider 'teclaw' is not registered; refuse to re-run create
  rollout`). The async allocation thread catches it and marks the bot `FAILED`.

The net effect is destructive: the container is gone, the bot is `FAILED`, and
the binding still points at the destroyed container. Restart does not merely
fail to work — it breaks a previously healthy bot.

The delete-and-recreate shape cannot work for teclaw in the first place. A
teclaw container is provisioned only inside `TeclawProvisionService.provision`,
called only from `create_bot`. There is no "start a teclaw container" primitive
outside bot creation, so the second half of delete-and-recreate has nothing to
call.

Restarting the container in place was considered and rejected: the teclaw side
owns the container lifecycle, and there is no confirmed restart semantics for a
teclaw container from the backend. Re-delivering config (`update_teclaw_bot`) is
a different operation and does not answer "my bot is stuck".

The policy this establishes is not new, and neither is the bug. Two callers
already guard against it locally:

- `BotPublishService.upgrade_bot_to_service` skips the restart entirely for
  teclaw bots, with a comment describing this exact failure — *"一旦重启会
  destroy 容器 + 重新分配失败,把 bot 打成无 binding 的坏状态,并丢掉个人阶段的
  容器内文件"* — and a ticket reference (Dima 2026070100117117968).
- `CreateBotForOthersService` refuses the operation outright, raising
  `"teclaw 类型的 Bot 不支持重启"` with a 400.

Both are workarounds at the call site. The destructive path itself was never
closed, so every other entry point — the three `/api/bots` restart endpoints and
the OpenAPI v1 one — still reaches it. This change closes it at the source, in
the lifecycle service, where no future caller can miss it.

## User Stories
- As a teclaw bot owner, when I click restart, I want to be told plainly that
  restart is not supported for my bot, so that I do not lose a working container
  to an operation that was never going to succeed.
- As a teclaw bot owner, I want a restart request to leave my bot exactly as it
  was — same container, same binding, same status — so that a mistaken click
  costs me nothing.
- As an operator using the admin and scheduler restart endpoints, I want teclaw
  bots rejected with a client error rather than a 500, so I can tell "not
  applicable" apart from "something broke".
- As an engineer, I want the rejection to sit at one place in the lifecycle
  service rather than at each delivery surface, so no future entry point can
  reach the destructive path by omission.

## Acceptance Criteria
- [ ] A restart request for a teclaw bot performs **no** writes: no device
      release, no BaaS `destroy_bot`, no binding status change, no bot status
      change, no restart-lock acquisition, no device allocation.
- [ ] A restart request for a teclaw bot is rejected with
      `BotOperationNotAllowedError` carrying a user-facing message stating that
      teclaw bots do not support restart.
- [ ] All four HTTP restart surfaces report that rejection as a client error
      (400 / the OpenAPI envelope equivalent), not a 500:
      `POST /api/bots/{bot_id}/restart`,
      `POST /api/bots/{bot_id}/restart-for-others`,
      `POST /api/bots/restart-scheduler`,
      and the OpenAPI v1 `POST /v1/bots/{bot_id}/restart`.
- [ ] A teclaw bot in any restart-eligible lifecycle state (`ACTIVE`, `FAILED`,
      `PENDING`) is rejected identically — the guard does not depend on the bot
      having a live binding.
- [ ] Restart behavior for non-teclaw bots is unchanged: BaaS bots still restart
      in place, arca bots still go through `stop_bot` + `start_bot`, and desktop
      bots are still rejected by the existing guard.
- [ ] The web client surfaces the rejection message and does not optimistically
      show the bot as restarting.
- [ ] The internal caller in `CreateBotForOthersService` continues to report a
      client error for teclaw bots rather than propagating an unhandled
      exception.

## In Scope
- A teclaw guard in `BotService.restart_bot`, placed before any state mutation.
- Tests proving the guard is inert (no release, no destroy, no status writes)
  and that it fires across lifecycle states.
- Tests pinning the client-error mapping on all four HTTP restart surfaces.
- Verification that the `CreateBotForOthersService` caller behaves correctly.

## Out of Scope
- **The recovery path for an unhealthy teclaw container** — tracked in #869.
  This change removes a destructive operation; it does not add a replacement.
- **Gating the restart affordance in the web client** — tracked in #869. The
  existing client already surfaces a rejected restart correctly, so no frontend
  change is required for the acceptance criteria above.
- **Restarting a teclaw container in place** via `update_teclaw_bot`. Not
  pursued; see #869 question 3 if this is revisited.
- **Removing `"teclaw"` as a `device_provider` value.** The value conflates
  "who owns the device lifecycle" (where teclaw does not belong) with "what
  flavor of container this is" (where it is meaningful). Untangling it is a
  separate refactor with a data migration; this change deliberately keys its
  guard on the bot's engine instead, so it adds no new reads of the provider
  axis.
- Cleanup of bots broken by the previous behavior — already recovered manually.

## Open Questions
None blocking. The two questions raised during design — the recovery path for an
unhealthy teclaw container, and whether `update_teclaw_bot` can cycle one — are
captured in #869 and do not gate this change.
