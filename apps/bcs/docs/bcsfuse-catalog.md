# BCSFuse catalog integration

Select the catalog protocol for each BCS deployment:

```toml
[bcsfuse]
enabled = true
api_version = "v2"
url = "https://bcsfuse.example.com"
```

`api_version` accepts `v1` and `v2`; omission defaults to `v1`. Restart BCS
after changing it. `enabled = false` disables remote integration. The existing
credential reference and request timeout settings apply to both versions.

| Operation | v1 (existing deployments) | v2 (Card deployments) |
| --- | --- | --- |
| Register/update Agent | `POST /v1/workers/{id}/sync`, availability update | `PUT /api/v2/agent-cards/{id}` |
| Remove Agent | `DELETE /v1/workers/{id}` | `DELETE /api/v2/agent-cards/{id}` |
| Register/update/remove Group | No catalog publication | Same Card endpoints, `resource_type=group` |
| Bot search | Existing `POST /api/v1/recommend` | `POST /api/v2/search`, `resource_type=["agent"]` |
| Worker tags | Existing batch Worker query | Empty tags; no Worker API request |

The selector switches registration, deletion and discovery together. BCS never
falls back from a v2 HTTP error to a v1 endpoint. Existing local name-search
degradation remains available. The two remote catalogs are independent; this
setting does not migrate or dual-write their records.

The v2 adapter uses the Fuse search-core response
`items[].{id,name,summary,score,score_source}`. It maps IDs, scores and summaries
into BCS's existing discovery/actor response types, including the legacy
`context.recommend_response.recommendations` shape. The frontend and BCS API
paths/response fields are unchanged. Fuse owns relevance ordering; BCS retains
its local visibility/relationship checks. `recommend_min_score` applies to
`score_source=reranker`; RRF ranking scores use a different scale and are kept.

The agreed Card URL prefix is `/api/v2/agent-cards`. Deploy this mode with a
Fuse catalog application supporting the labels/filter contract below and
`/api/v2/search`. Group publication requires the generic Agent/Group Card
application; the TC-specific Card entry point currently accepts Agents only.
Card-only deployments use BCS's
local context fusion service, since their API has no legacy Group-fusion route.

## Published data

- Agent: BCS bot ID, display name, summary, skills and domain tags. The Card
  description combines the summary and domains so the default Fuse converter
  indexes domain-only Agents too. All three bot visibility values and all three
  user visibility values are synchronized. Human actors and deleted Agents are
  removed. BCS still enforces collaboration permission checks on search results,
  including allowing a private Agent that is already a friend.
- Group: BCS group ID, label and context, with `resource_type=group`.
  Only normal Groups with `record_status=active` and `visibility=public` are
  published. DM and private Groups are removed. Publishing does not change
  Group visibility or member visibility. Conversation completion does not
  remove a still-public Group resource.
- Agent/Group updates explicitly send empty descriptions and arrays when
  cleared, because Fuse recursively merges objects and preserves omitted
  fields. Runtime credentials, local context files and chat history are not
  part of the discovery projection.

### Labels contract

Agent writes carry both current, committed control-plane visibility values:

```json
{
  "resource_type": "agent",
  "labels": {"visibility": ["private"], "user_visibility": ["protected"]},
  "card": {"name": "Billing", "description": "Invoices", "skills": []}
}
```

`labels` is a map of string keys to string arrays, suitable for additional
static classification tags without adding a top-level field for each category.
BCS owns and sends only these keys:

| Resource | Label | Values |
| --- | --- | --- |
| Agent | `visibility` | One of `public`, `protected`, `private` |
| Agent | `user_visibility` | One of `public`, `protected`, `private` |
| Group | `visibility` | `public` on every published Group |

The Card body has no duplicate visibility field. Groups have no
`user_visibility` label. `group_kind`, `record_status` and conversation status
determine publication eligibility locally; they are not labels. A Group's
`label` is its display name, not a classification label.

For `user_visibility`, `private` hides the Bot from user discovery, `public`
allows discovery and friendship applications, and `protected` still allows
discovery but restricts friendship applications to the configured organization
scope. `friend_ext.view_scope_user_friend_deps` and the bot-side organization
scope remain BCS friendship policy; they are not sent to Fuse. Pending approval
metadata likewise does not override committed visibility values. No department
field is required in Card registration, including for `protected`.

The agreed Fuse label update semantics preserve omitted keys, replace the
array of a supplied key, and remove an ordinary label when supplied as `[]`.
Required visibility labels cannot be removed. BCS sends the full current value
of both Agent visibility labels on every write, without clearing unrelated labels.

## Search parameters and defaults

BCS calls v1 `POST /api/v1/recommend` with `question`, `topK` and `min_score`,
without explicit filter parameters. Fuse v1 defaults restrict results to
`availability` in `public`/`protected` and `runtime_state=online`. V1 registers
private Agents too, but these defaults exclude them from semantic retrieval.
V1 `availability` corresponds to bot `visibility`; the user publication flow
maps to online/offline and does not retain the public/protected distinction.

The agreed v2 defaults apply to `/api/v2/search` and `/api/v2/recommend`:

| Parameter | When omitted | BCS search request |
| --- | --- | --- |
| `query` | Required, nonblank | Search text |
| `limit` | `5` | Requested candidate count, at least `1` |
| `filters` | `{}` | `{"resource_type":["agent"]}` |
| `filters.resource_type` | All resource types | Agents only |
| `filters["labels.visibility"]` | All visibility values, including private | Omitted |
| `filters["labels.user_visibility"]` | All visibility values | Omitted |
| Other label filters | Unrestricted on omitted keys | Omitted |

Within one filter key, values are ORed; different keys are ANDed. An explicit
empty array matches nothing. Omitting a key does not impose a public-only
default. Label filters use `labels.<key>` (for example, `labels.department`);
`resource_type` remains a separate filter key. These HTTP APIs currently serve BCN; external Spring/Sofa RPC entry
points apply their own discovery policy, without a caller visibility option.

BCS currently uses `/api/v2/search`. It retains self/friend/collaboration checks
on the returned candidates, without refilling after filtering. The legacy
`context.recommend_response` remains populated before these local checks;
no additional filtering is applied to that context in this change.

## Publication lifecycle

Successful core writes enqueue the resource identity after the storage commit.
The selected v2 lifecycle reads current BCS state immediately before each
publication. It coalesces repeated changes and serializes HTTP writes. Registration,
profile/capability changes, bot visibility changes, user-visibility-only patches,
deletion and Group mutations all use these existing change events. Agent
visibility changes send PUT; a Group becoming private or being deleted sends
DELETE. Fuse's accepted response confirms saving; indexing
and search visibility are asynchronous.

Like v1, the instance handling the change publishes asynchronously, without
master election or startup/master-acquisition scans. Each change gets at most
10 attempts, using `sync_retry_base_ms` exponential backoff capped at 8 seconds.
Exhaustion is logged; a subsequent change starts a new attempt budget. Every
attempt re-reads current state, so a retry after deletion sends DELETE instead
of replaying a stale PUT. Read failures are retried rather than treated as deletion.

Each drain takes at most 100 identities and permits one HTTP request in flight.
Each Agent attempt reads one control-plane row by bot ID and environment
(`LIMIT 1`), including both visibility fields. Each Group attempt uses its
existing current-state lookup (cache hit or DB read). There are no page/count
queries or lease checks, and no database lock spans an HTTP request. A failing
Agent change therefore costs at most 10 single-row reads and 10 HTTP attempts;
newly committed changes may legitimately start another budget.

Pending identities are held in process memory. Failed or interrupted changes
can be republished operationally using the same Card ID. Switching an existing
deployment between independent catalogs requires an explicit initial migration
and reconciliation before routing discovery traffic to the destination.

## Validation

```sh
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-config-api -p bcs-fuse-client -p bcs-fusion
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --test integration_fuse_cards
```

The Singlebox `stories` suite also runs `scripts/e2e-test/fuse_cards_story.py`
against an isolated production BCS process configured for v2. It uses a local
HTTP catalog peer to verify Agent/Group publication, all visibility values,
private-friend search and the existing response shape, failed acknowledgements,
bounded retries, deletion during retry, and restart without backfill. The process
inherits `LLVM_PROFILE_FILE`, so these requests contribute to the existing E2E
coverage gate alongside the shared stack's v1 scenarios. Run it directly with:

```sh
python3 apps/bcs/scripts/e2e-test/fuse_cards_story.py --binary apps/bcs/target/debug/bcs
```
