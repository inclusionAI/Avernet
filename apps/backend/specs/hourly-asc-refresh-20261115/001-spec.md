# Hourly ASC Caller refresh

## Scope
An independent enterprise lifecycle scans hourly, refreshing only Caller instances whose installed-token expiry is unknown, expired, or at most24hours away. Reuse installation fencing/expiry transaction, runtime-health and enabled-Cron checks; exclude Teclaw. Owner Passport callbacks cease driving Caller refresh. Manual/OpenAPI refresh remains available.

## Public contract
ExpertChatInstanceRepository.list_caller_token_refresh_candidates(expires_before,after_id=0,limit=100) returns current-environment init/success/active rows with unknown or <=cutoff expiry, ascending id keyset pagination. Cutoff must be timezone-aware and converted to UTC; after_id>=0; 1<=limit<=1000. Returned dictionaries include existing row identity, ext/binding and UTC expiry; enterprise consumer validates Bot ownership/engine/actual binding. No new schema or enterprise SDK in public source.

## Compatibility
Keep optional BotService refresh-port constructor wiring for legacy enterprise DI compatibility, but Owner callback no longer reads/invokes it or its rollout switch. Existing Owner result/error behavior unchanged.

## Validation
RealSQLite exact24h threshold, NULL/expired, future exclusion, environment/status isolation, pagination and invalid arguments; Owner route remains successful without invoking Caller including failing optional ports; Owner errors propagate.
