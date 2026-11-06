# Plan: optional viewer scope for task semantic search

Status: approved by Shangjian on 2026-11-06; implemented and locally verified; not deployed.

## Approach
Extend the existing request and service method with an optional pair. Preserve the
existing no-viewer call path exactly, including calls to legacy implementations
that only accept the old signatures. Explicit viewers flow to the existing Bot
Discover service; no second protocol, direct upstream URL, or alternate route.

## HTTP contract diff (requires owner approval)
```diff
 POST /api/v1/collaboration/tasks/search
 {
   "query": "测试发布的bot",
   "task_id": "<task>",
   "node_id": "<current-node>",
-  "relay_token": "<current-generation>"
+  "relay_token": "<current-generation>",
+  "viewer_actor_type": "bot", // optional: bot | human
+  "viewer_actor_id": "<viewer-id>" // optional, paired with type
 }
```
The four original fields remain required. Both viewer fields omitted/null retain
old behavior. A partial pair, blank identity, invalid type or non-string identity
returns the existing validation-error format with HTTP 422. Normalize surrounding
identity whitespace consistently with the product endpoint. Bot IDs use the full
external bot UUID; do not derive or replace a caller's supplied viewer identity
from task owner fields. No new required headers or auth mode.

Response is unchanged:
```json
{"code":200000,"message":"OK","data":{"candidates":[
  {"bot_id":"<id>","name":"<name>","description":"<text>","score":0.9}
]},"request_id":"<trace>"}
```

## Protected service contract diff (requires owner approval)
```diff
 async def search_bots(
     self, *, query: str, task_id: str, node_id: str,
+    viewer_actor_type: Literal["bot", "human"] | None = None,
+    viewer_actor_id: str | None = None,
 ) -> list[BotCandidate]: ...
```
Update the one service Protocol and its existing implementation, not a new wrapper.
Propagate the optional pair through TaskSearch.search_catalog/search,
search_candidates and SemanticKeywordBotDiscover.search_full_query/_semantic_search.
When absent, invoke the established signature with no added keywords. Validate the
pair at the public DTO and service boundary so direct callers cannot half-scope.
Do not silently drop a requested viewer in an adapter that cannot implement it.

## Behavior matrix
| Mode | Recommendation filters | BCS catalog filters |
| --- | --- | --- |
| omitted (unchanged) | runtime_state=[online] | visibility=[public], user_visibility=[public,protected,private], status=online; current tc_bot behavior |
| bot viewer | availability=[public,protected], runtime_state=[online,offline] | visibility=[public,protected], viewer_actor_type=bot, viewer_actor_id supplied; product tc_bot behavior |
| human viewer | availability=[public,protected,private], runtime_state=[online] | user_visibility=[public,protected], viewer_actor_type=human, viewer_actor_id supplied; existing BCS Human defaults |

Delegate explicit perspectives to BotDiscoverService's existing viewer derivation
instead of copying that matrix into a second policy implementation. In particular,
call search_by_keyword with runtime_state="online" and the explicit viewer pair;
do not also pass _DISPATCH_CATALOG_FILTERS, which would override the viewer scope.
Preserve _runtime_ready and maximum five returned task candidates in all modes.
BCS keeps authority over catalog membership and relation semantics. The viewer
pair is a query perspective, not proof of caller identity or permission to execute.
Existing internal-network admission and relay-generation checks remain mandatory.
No changes to task/node/assignee fields, event branches, report fields or S1-S6.

## Files and seams
- apps/backend/src/agentclaw/community/core/task/domain/search_viewer.py:
  pure shared paired-scope validation; no request identity inference.
- apps/backend/src/agentclaw/community/adapters/http/task/schemas_relay_search.py:
  add optional fields and paired validation; preserve extra=forbid.
- apps/backend/src/agentclaw/community/adapters/http/task/relay_routes.py:
  pass viewer pair after existing graph/node/generation checks; do not resolve a
  different execution identity or relax token checks.
- apps/backend/src/agentclaw/community/core/task/task_service_protocol.py and
  task_center/task_service_relay.py: implement the reviewed signature above.
- apps/backend/src/agentclaw/community/core/task/task_dispatch/search.py and
  task_runner/client/{candidate_search,semantic_keyword_bot_discover}.py:
  propagate explicitly requested scope without altering no-viewer behavior.
- Existing public Bot discover/catalog services remain the policy source of truth.
- apps/backend/tests/community/adapters/http/task/{test_schemas,test_router}.py,
  core/task/task_runner/integration/test_semantic_keyword_bot_discover.py and
  relevant TaskService tests: request validation, propagation, default compatibility,
  visibility/runtime filtering, generation rejection and unchanged response DTO.

## Skill source changes (only after approval)
Source of truth:
/Users/shangjian.msj/Github/aiworkbench/skills/infra/task-loop/
```diff
 POST_REQUIRED_FIELDS[".../search"] = {"query","task_id","node_id","relay_token"}
 POST_ALLOWED_FIELDS[".../search"] = {
   "query","task_id","node_id","relay_token",
+  "viewer_actor_type","viewer_actor_id",
 }
```
As refined by Shangjian, scripts/relay_validation.py requires type exactly bot
and a nonblank string ID for every Skill search; missing/null and Human viewers
are rejected locally. Backend retains optional Bot/Human support for other clients.
Update S5c examples and tests to always pass the current executor Bot Runtime
identity. Resolve it before search; never infer from task owner or candidates and
never fall back to an unscoped request. The transport validates shape, not identity
provenance; the Skill caller must obtain the explicit runtime identity.
Do not modify installed Skill copies or sync destinations.

## Reviewed documentation synchronization (after approval only)
Update the existing search HTTP and service definitions in the protected
bot-s1-s8-functional-contract.md, end-to-end-relay-design.md and applicable task-cli
design.md sections to this same optional extension. Do not rewrite unrelated
sections or introduce a competing protocol. This draft does not modify them.

## Data/dependencies/deployment
No persistence schema, migrations or new packages. Deploy Backend optional-field
support before clients opt in; older backends reject these fields. This work does
not itself deploy, push commits, or execute a real Relay task.

## Risks and tests
- Absent scope must not become bot scope: pin old keyword calls and default filters.
- Viewer policy must not be overridden by public-only filters: capture actual
  recommendation and BCS parameters for both types in isolated tests.
- Human-visible and Bot-visible sets differ: fixtures include opposite visibility /
  user_visibility, plus private and protected entries and friend markers.
- Preserve task runtime-readiness filtering and cap even when product Bot semantic
  recall includes offline entries. Explicitly test this distinction.
- Validation failures must not send upstream requests or leak tokens.
- Keep other in-progress regression and Cookie-retention changes outside this patch.


## Implementation notes
- Paired HTTP validation runs on the viewer ID field (including its default),
  rather than a whole-model validator: malformed viewer input must not echo the
  complete body including relay_token in FastAPI's 422 response.
- New end-to-end isolated coverage is in
  apps/backend/tests/community/core/task/task_runner/integration/test_task_search_viewer.py.
  It exercises the actual TaskService/semantic/Product Discover/BCS parameter
  mapping; only external recommendation and BCS responses are synthetic.
- task-cli design.md contains no search interface definition to update; the
  existing functional contract and end-to-end service/HTTP definitions are updated.
