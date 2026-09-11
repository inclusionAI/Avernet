# Bot heartbeat state

BCS accepts the existing WebSocket `bot.status` and HTTP `POST /bots/status`
payloads, but repositories retain only `last_heartbeat` for registration liveness.
The legacy payload fields `status`, `dynamic_summary`, `load`, and `updated_at`
are not stored in a registration, database, or external cache. Discovery does
not match against heartbeat descriptions; static capability matching is unchanged.

## Liveness and discovery

Registration and reconnection initialize the process-local timestamp. A status
update renews it for a known in-memory registration and returns `false` for an
unknown registration. The timestamp uses a monotonic clock; the inbound
`updated_at` value does not control liveness.

The existing expiry policy is unchanged: the persistent repository considers a
registration expired after 300 seconds without renewal. Its memory-based active
and discovery paths filter expired entries; detail reads may fall back to the
database. The memory implementation retains its existing special case for
disconnected registrations with session tokens. This change neither schedules
`cleanup_expired()` nor changes WebSocket disconnect handling.

User-facing `active`/`offline` status remains computed from the actor lifecycle
and WebSocket connection or configured HTTP Provider delivery target. It is not
the client's reported `idle`/`busy`/`error` state and does not use `load`.

## Protocol compatibility

| Surface | Behavior |
| --- | --- |
| WebSocket `bot.status` | Existing payload accepted; success acknowledgement remains `{"updated":true}`. |
| `POST /bots/status` | Existing payload accepted and echoed in the response's `status` object; echoing does not retain state. |
| `/bots/query`, `GET /bots/{id}`, other online-state views | Existing computed `dynamic_status.status` remains `active` or `offline`. |
| `POST /providers/agentpass/resolve` diagnostic endpoint | `bot.dynamic_status` is omitted. Identity, capabilities, binding and other registration fields remain. |

`RegisteredBot` no longer has a `dynamic_status` field. Rust consumers should
remove that initializer or access, and use the online-state query services where
they need availability. The separate inbound `BotDynamicStatus` and outbound
`DynamicStatusResponse` types remain available for their existing protocols.

## Deployment

No status hashes are read or written, including during DB fallback and cold
reconnect. Old `bcs:status:*` keys (or the configured prefix equivalent) expire
using their existing TTL; no explicit deletion or migration is required.
Other cache consumers, including election, are unaffected.

`PersistentBotRepo::new(db)` uses the MySQL dialect;
`PersistentBotRepo::with_sql_flavor(db, flavor)` selects a dialect explicitly.
The old cache/prefix constructors and the unused legacy database-name argument
are removed. The repository has no cache-plugin dependency, and its tests need
only a database. Composition roots must use these DB-only constructors; the
shared bootstrap and all in-repository callers are updated. OCB internal source
has no direct constructor calls and consumes the shared bootstrap.
