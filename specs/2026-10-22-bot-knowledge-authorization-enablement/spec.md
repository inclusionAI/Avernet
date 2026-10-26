# Bot knowledge selection

Persist only current application enablement. The external platform owns grants,
currently addressed by business bot_id plus the existing user identifier. An
external reserved bot_uuid is NOT an active contract: no generation, Header,
source primary-key substitution or UUID lookup. Existing MCP headers and values
stay unchanged. Service Bots edit only in the current draft; other Bots do not
require publication state. No knowledge versions, release hooks, history tables
or new membership synchronization.

Space changes do not inherit another space's selection. A hidden other-space
row can be replaced through the absent revision (zero); its stored revision
still increases to reject stale writes. Enabling always checks the current
space's catalog. Old-space rows do not count toward the current enabled limit.
Disabling an absent relation creates no record and returns revision zero.

## Reviewed storage contract (2026-10-26)

Current selection uses the user-reviewed OB schema: database-generated
`gmt_create TIMESTAMP` and `gmt_modified TIMESTAMP`, signed BIGINT revision,
unchanged business fields and index columns, and Chinese schema comments.
Creation time is preserved when selection changes; modification time updates.
These metadata fields do not expand the HTTP response contract. The OB SQL
records GLOBAL secondary indexes; local ORM schema creation remains portable.

The ac_bot_publish index changes have not been executed and are deferred for
separate target-index/query-plan review and deployment approval. Keep them in a
separate review-only SQL file, not the current-selection table creation script.
The deferral does not remove draft permission checks or the indexes declared for
fresh local test schemas, and it is not evidence of production query performance.
