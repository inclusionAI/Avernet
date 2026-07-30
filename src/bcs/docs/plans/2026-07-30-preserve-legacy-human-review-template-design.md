# Preserve Legacy Human Review Template Design

## Context

PR #514 changed the existing `bot-human-bot-review` seed from an unassigned
`human_input` node into a DingTalk-specific, directly assigned node backed by
the new `$authenticated_human` runtime placeholder. That was not required by
the BCN OpenAPI v1 Group resource slice and changed the prerequisites of
existing Legacy state-machine entry points.

The approved compatibility rule for this PR is that the new V1 API must not
change existing Legacy behavior.

## Decision

Restore both localized `bot-human-bot-review` seed files to their pre-PR
semantics:

- no `human_input_channel`;
- no Human `assignee`;
- no direct notification policy;
- any authenticated Present Human session participant remains eligible to
  respond under the existing runtime rules.

Remove the `$authenticated_human` placeholder resolver and its dedicated tests.
That capability was introduced only to repair the hard-coded Human actor added
by the mistaken seed change, and is not required by the V1 Group API.

Keep the generic `requires_human_input_channel` configuration outcome and V1
deferred-start behavior. Those features apply to any explicitly
channel-enabled collaboration definition and do not require changing the
Legacy seed.

## Compatibility

- Existing template discovery returns the original template shape.
- Existing Legacy Group creation and StateMachine run entry points retain
  their original Human participation requirements.
- Service invocation no longer becomes incompatible merely because a Group
  uses the shipped `bot-human-bot-review` seed.
- Explicit custom definitions that declare `human_input_channel` continue to
  use the V1 deferred-start path.

## Verification

- Assert both template service and admin seed-loader projections expose an
  unassigned Human review node with no channel configuration.
- Run collaboration template, admin seed-loader, and collaboration runtime
  tests.
- Run the focused V1 Group tests that cover channel-enabled deferred startup.
- Confirm production, test, and seed paths contain no `$authenticated_human`
  placeholder.
