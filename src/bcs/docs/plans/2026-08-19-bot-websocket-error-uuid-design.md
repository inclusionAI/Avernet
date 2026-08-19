# Bot WebSocket Error UUID Logging Design

## Problem

Bot WebSocket read errors currently log only the transport error. The handler keeps
the successfully registered Bot UUID, but provider delivery is rejected before that
registration state is set. As a result, the production `Connection reset by peer`
error cannot be attributed to a Bot even when the preceding `bot.connect` frame
declared one.

## Design

Keep two connection-local identity states in the WebSocket delivery adapter:

- `observed_bot_uuid`: the UUID declared by the initial `bot.connect` frame, or the
  UUID resolved by the Bot runtime when the frame did not declare one.
- `registered_bot_id`: the existing state that is set only after streaming
  registration succeeds and continues to drive cleanup behavior.

The dispatcher exposes an internal entry point that updates the observed identity
while preserving the existing public `dispatch_frame` API. Provider rejection may
therefore leave `registered_bot_id` empty while retaining the UUID needed for
diagnostics.

Bot WebSocket reset and general error events log structured fields:

- `bot_uuid`: the observed UUID, falling back to `"unknown"` before an initial
  `bot.connect` frame is received.
- `registered`: whether streaming registration completed.
- `error`: the existing transport error.

The change is limited to `bcs-ws`; it does not alter authentication, provider
selection, registration, close-frame behavior, or cleanup.

## Testing

Extend the provider-delivery compatibility test to call the identity-aware
dispatcher entry point and assert that provider rejection records the attempted Bot
UUID without marking it registered. Add focused unit coverage for the log identity
fallback so pre-connect failures remain explicitly attributable only to `unknown`.

