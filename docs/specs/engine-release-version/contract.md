# Engine release version inspection (v1)

## Scope and ownership

`GET /openapi/v1/bots/{bot_id}/engine-version` reads a personal Bot or service
Bot draft. It returns release facts and restart advice, without restarting,
writing configuration, acquiring Pins, or changing a publication. Frontend is
out of scope. The owning Service API is
`core/bot_management/engine_version/protocol.py`, re-exported from
`api/engine_version_service.py`; HTTP owns envelope/error translation. All
profiles use the same business service and existing device transport binding.

Both `user_id` and the optional addressed owner (`entity_id`) follow the existing
public identity/grant contract. The owner or an explicit MEMBER collaborator
can inspect; no restart authority is granted. Public visibility and Space
membership alone do not grant device inspection. Unauthorized/missing Bots are
masked as 404. Stage other than draft is 409. `device_uuid` selects an instance;
multiple devices without a selection, or an unknown selection, are 409. Provider
failure is detection failure rather than instance fallback. No public command,
path, variable name, binding id, or target-version parameter is accepted.

## Release identity

The complete release branch is the source of truth. Backend and Engine are
released together: Backend bakes `TARGET_ENGINE_VERSION` and the paired Engine
bakes `AGENTCLAW_IMAGE_VERSION`. Rebuilding the same branch is the same identity.
Configuration loading/DI reads the target once and injects it into the service;
core never reads the process environment.

A fixed existing device request reads only `printenv AGENTCLAW_IMAGE_VERSION`
through `POST /api/bash/exec`, with process timeout 5s, transport timeout 10s.
The command runs at `/home/admin/`. Deployment types without that directory or
endpoint return read_failed, not a claimed legacy version. No Engine API is
added, so existing runtimes can be inspected without restarting them.

A successful envelope, integer exit code and string stdout/stderr are required.
Exit 1 with empty stdout/stderr means variable absent. Exit 0 with an empty
value, or historical marker `0.1.153`, is legacy. Other failures, malformed or
multiline output, stderr, timeout, HTTP 404/501 and unavailable capabilities are
read_failed. Errors return controlled reasons, never upstream addresses,
credentials or stderr. The marker is trusted as a release artifact; deployment
must not independently override it. This advisory does not read template or image policy.

An empty/invalid target or `0.1.153` makes target unavailable. No guessed latest
version or development fallback is used. Full branch equality is same, including
non-REL branches. Unequal branches are ordered only when both strictly match
REL plus a valid Gregorian YYYYMMDD. Suffixes are never stripped. Other unequal
branches are unknown. A newer current release is never advised to downgrade.

## Result (existing Envelope.data)

- `detection`: status known/legacy/read_failed/not_applicable, optional controlled
  reason. Only active_engine=openclaw/hermes is in scope. All other engines
  return not_applicable/engine_not_supported before any binding, device-list,
  connection-resolution or probe call. Authorization and draft checks still run.
- `current`: known `release_id` or historical `raw_version`, source engine_env;
  omitted when unidentified. Missing metadata does not invent a release number.
- `release_target`: status available/unavailable; available includes release_id
  and source backend_image_env; unavailable includes a controlled reason.
- `comparison`: older/same/newer/legacy/unknown.
- `restart_hint`: recommended/restricted/none/unknown; optional restart_reason.
- `context`: bot_id, draft stage, checked_at UTC, selected instance_id when known.

Older/legacy plus a valid paired target returns restart_hint=recommended.
Same/newer returns none. Failed reads, an unavailable target, or incomparable
branches return unknown. Provider, Bot ext Pin, explicit template images and
BaaS stored image configuration do not restrict this advisory and are not read
as policy inputs. Restricted and restart_reason remain in the response schema
for compatibility, but this implementation does not emit them. No lifecycle,
image-policy, or publication behavior is changed by the query.

For engines outside OpenClaw/Hermes (including Teclaw), detection is
not_applicable/engine_not_supported, restart_hint is none, current and
context.instance_id are omitted. Comparison is unknown solely because no
comparison was attempted; clients must interpret detection and restart_hint.
This outcome also applies when the paired target is unavailable.

## Deployment and compatibility

The supported deployment model uses the paired Engine images independently of
the binding provider. There is no provider allowlist or remote image-adoption
attestation in the inspection path. Provider still selects the existing
transport and instance-selection mechanism; it does not alter restart advice.
This endpoint grants no restart authority or guarantee of an actual upgrade.

The target is the release paired with this serving Backend image; it is not a
registry lookup or a promise that every external template was updated. Release
acceptance must ensure the default rebuild templates actually adopt the paired
Engine image and neither marker is independently overwritten. This check does
not fetch all remote templates or mutate Pin state. On rolling release, targets
can differ temporarily by Backend instance; responses always report the target
used. A rollback does not recommend downgrading a newer Bot.

No database migration, CI reporting endpoint, CommonConfig value, digest or
source-fingerprint mapping is introduced. Old Bots with missing/empty metadata
or the historical default remain legacy. Unsupported old endpoints and failed
reads remain read_failed. The additive API works even when build parameters
are omitted, with target unavailable until deployment supplies them.

## Validation

Service and signed HTTP contract suites cover identity, old-Bot compatibility,
access, stage/instance selection, command failures and image restrictions.
The endpoint framework exercises real persistence + DI. Architecture gates
register the new service and public operation. Local simulator additionally
verifies actual image ENV, running Backend ENV/image id and Gateway -> Backend
-> Engine HTTP observations. Local fixture branch labels are test inputs;
actual release pipeline expansion/template adoption require release acceptance.
