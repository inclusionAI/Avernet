# Online conversation catalog and friend runtime addressing

## Requirement

The conversation workspace needs online service Bots without discarding personal
Bots. Operator/management views must retain their existing unfiltered default.
Friend chat must not reject an explicit online target or address a draft runtime
through another part of the conversation chain.

## Catalog contract

`GET /openapi/v1/bots` and `GET /openapi/v1/bots/collaborations` accept an optional
`stage=draft|verify|online`. Omission preserves existing filtering and pagination.
An unknown value is a validation error. Stage only filters `bot_type=service`;
personal/other kinds retain their existing inclusion rules for any valid stage.

Stage availability uses the same publication-selection function as runtime
addressing, scoped by the authorized Bot's **primary key** and current deployment
environment. It is not a match against the latest publication's status:

- draft: a positive workspace binding on the source Bot;
- online: the newest successful publication has a positive `binding.online`;
- verify: the newest validating publication has `binding.verify`; otherwise the
  newest successful publication may retain an ACTIVE verify binding.

An unbound newest eligible publication does not fall back to an older eligible
publication. Publication ordering is creation time descending, then ID descending
to make simultaneous timestamps deterministic. Filtering precedes `count`,
offset and limit. Existing owner, tenant, environment, soft-delete and application
grant restrictions are retained. Collaborators are still selected by stable Bot
primary key; owners are not added to the collaboration collection.

`POST /openapi/v1/bots/metadata/queries` keeps its exact pair lookup and does not
filter draft records. Its display-safe response adds:

- `online_available`: boolean for service Bots, null for other kinds;
- `live_version`: the available online publication's version, otherwise null;
- `conversation_stage`: online for service Bots, draft for other kinds.

Availability means a published target exists, **not** current device health,
friendship, collaboration permission or a promise that connection will succeed.
No binding IDs, publish ext, configuration snapshots or credentials are added to
the metadata response. Clients must check service availability separately from
the recommended stage; an unavailable service is not redirected to draft.

## Friend runtime contract

Explicit `f_user_id` retains BCN-authoritative friendship and actor restrictions:
only that Human or the Bot owner may inspect that Human's session index.
Backend-owned session records, not textual user-ID suffixes, establish ownership.

For service Bots, both explicit online and legacy draft friend requests mean the
published online conversation target. Verify is rejected. If no successful online
target exists, respond with the existing stage-not-live conflict before resolving
a device or provisioning a Caller instance. No fallback to the source workspace.
Personal friends retain draft-only runtime addressing.

Session operations, connection and model reads share the friend stage guard.
Shared service connections use the canonical online binding. Caller-mode chat
continues to use the subject's independent instance after verifying the online
publication; owner inspection must not select the owner's Caller instance.
Explicit per-container selection remains forbidden for friend requests.

File list, materialization status and content reads accept the same optional
friend selector and stage semantics. They validate friendship and the owned
session before reading resource metadata or bytes. Existing resource ID + owner
+ Bot + session checks remain in the resource service. Upload, completion and
deletion do not acquire friend permissions in this change.

`POST /openapi/v1/bots/{bot_id}/iam-token` accepts optional `f_user_id`, `owner_id`
and `session_id`. In explicit friend mode, the actor must be the subject, the
session is required and verified, and stage is resolved by the same guard. The
Bot's actual entity address is read from the exact owner-scoped Bot, never guessed
from its owner or switched by a second client entity selector. Caller-token
installation targets only that subject's existing Caller instance; an owner
reading another Human's sessions cannot install the owner's IAM there. Normal
IAM requests retain their existing behavior. Optional `session_id` is forwarded
verbatim as session context for engines that require it.

## Compatibility and scope

No database migration, new deployment flag, frontend change, `/bots/all` change,
friend management privilege or application admission expansion is included.
Existing clients may omit stage. Legacy service friend draft requests already
meant published chat; this now also governs their model/IAM addressing rather
than accidentally selecting the draft workspace.

The frontend must opt into `stage=online` on owned/collaboration catalogs,
filter friend service metadata by `online_available`, and use online for service
conversation requests while keeping personal conversations at draft. Backend
support alone does not change the workspace's current request parameters.

## Verification

Regression coverage includes mixed pagination, newer drafts alongside online
versions, unbound/failed/deleted/foreign-environment releases, duplicate Bot IDs
under different owners, exact collaboration scopes, display-safe metadata,
friend online addressing, no draft fallback, independent Caller targeting,
foreign-session file rejection and IAM subject restrictions. Existing module,
repository/Service API conformance and OpenAPI authorization/admission inventory
gates remain enabled. Deployed BCN/device/IAM integration is not simulated by
these local tests and must be verified in a deployed environment.

### Local validation results

- Combined affected HTTP, domain, repository and architecture suites:
  **1887 passed, 1 skipped** (`pytest -q --no-cov`).
- Ruff passes for the changed production modules and other changed/new tests.
  `test_expert_chat_instance_service.py` has 16 pre-existing unused-local
  findings, verified identical in rule, message and line number against HEAD;
  no new lint finding was introduced and no check was disabled.
- Source line counts were checked. The Bot router retains its existing
  architecture allowlist waiver; no new waiver was added. Metadata models and
  the session-backend helper were extracted to keep their modules below the cap.
- `git diff --check` passes. Full Backend CI, remote integration, deployment,
  frontend migration, commits, pushes and PR creation were not performed.
