# Relay process-isolation repair

## Incident

The merchant-hybrid acceptance request reached the local BCS Provider bridge,
BaaS, Engine adapter and Claude relay.  The relay then received `SIGTERM` while
the model was streaming, closed its WebSocket, and caused the Engine adapter to
return `claude_code relay connection lost` to BCS.

## Requirement

The singlebox relay launcher must detach each owned relay from the initiating
shell's process group.  A lifecycle stop must still terminate the PID recorded
by singlebox.  Relay shutdown logs must identify the received signal without
logging message bodies, sessions, credentials, or model configuration values.

## Acceptance

1. The launcher uses a new session for each relay process and records that
   relay's PID.
2. Source changes rebuild the local gateway distribution before launch when
   necessary, so a running checkout cannot use stale generated JavaScript.
3. A targeted Claude chat reaches a terminal `final` response in the real
   `merchant_hybrid` topology after the restart. The acceptance task names
   every required output-card field explicitly, so the test verifies the
   contract instead of inferring field labels from free-form text.
4. Existing shell/profile tests and gateway tests pass.
