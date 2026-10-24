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
