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
its score threshold and local visibility/relationship checks.

The agreed Card URL prefix is `/api/v2/agent-cards`. Deploy this mode with a
Fuse catalog application exposing that prefix and `/api/v2/search`. The
search-core branch originally mounted Card writes under `/api/v1/agent-cards`;
the BCS adapter targets the agreed v2 prefix. Card-only deployments use BCS's
local context fusion service, since their API has no legacy Group-fusion route.

## Published data

- Agent: BCS bot ID, display name, summary, skills and domain tags. The Card
  description combines the summary and domains so the default Fuse converter
  indexes domain-only Agents too. `public`
  and `protected` Agents remain discoverable, as in existing BCS search.
  `private` Agents and Human actors are removed from the catalog. BCS still
  enforces the original collaboration permission checks on search results.
- Group: BCS group ID, label and context, with `resource_type=group`.
  Only normal Groups with `record_status=active` and `visibility=public` are
  published. DM and private Groups are removed. Publishing does not change
  Group visibility or member visibility. Conversation completion does not
  remove a still-public Group resource.
- Agent/Group updates explicitly send empty descriptions and arrays when
  cleared, because Fuse recursively merges objects and preserves omitted
  fields. Runtime credentials, local context files and chat history are not
  part of the discovery projection.

## Publication lifecycle

Successful core writes enqueue the resource identity after the storage commit.
The selected v2 lifecycle reads current BCS state immediately before each
publication. It coalesces repeated changes, serializes HTTP writes, retries
failures with a delay up to 30 seconds, and re-reads state on every retry.
Changing a resource to private or deleting it sends DELETE, rather than a
business `status` field. Fuse's accepted response confirms saving; indexing
and search visibility are asynchronous.

The worker runs on the existing BCS master. Startup/master acquisition backfills
existing discoverable Bots and Groups in pages of 100; each drain processes at
most 100 identities with one HTTP request in flight. SQL Bot page reads use the
existing page/count queries, Group page reads use the existing participant JOIN;
each publication adds one current-state lookup (Bot cache hit or DB read,
Group cache hit or DB read) and one lease check. No database lock spans the HTTP request.

Pending changes are held in process memory. Startup backfill republishes
existing discoverable resources; it does not reconstruct a failed deletion
after the BCS record is removed and the process exits. Such deletions need to
be retried operationally against the same Card ID. Switching an existing
deployment between independent catalogs likewise requires reconciling the
destination catalog before routing discovery traffic to it.

## Validation

```sh
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs-config-api -p bcs-fuse-client -p bcs-fusion
cargo test --manifest-path apps/bcs/Cargo.toml -p bcs --test integration_fuse_cards
```
