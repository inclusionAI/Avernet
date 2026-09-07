# ClawWeb shared OSS module

Server-only OSS access shared by Insight Center and future ClawWeb features.

Flow: ClawWeb -> local MOSN/Layotto (`127.0.0.1:11004`) -> Mist -> runtime AK/SK -> `ali-oss`.
Credentials are cached in memory only and must never be returned to the browser, logged, or persisted.

Defaults:

| environment | Mist mode | Mist resource name |
| --- | --- | --- |
| pre | `pre` | `other_manual_clawweb_agentclaw_oss_pre` |
| prod | `prod` | `other_manual_clawweb_agentclaw_oss` |

Shared overrides use `CLAWWEB_OSS_*` / `CLAWWEB_MIST_*`. Existing Insight Center deployments may continue using the backward-compatible `INSIGHT_OSS_*` / `INSIGHT_MIST_*` variables.

Insight Center selects the Evidence provider automatically: SQLite uses local files, while deployed MySQL/ZDAS runtimes use OSS. `INSIGHT_EVIDENCE_PROVIDER` is only an explicit test or emergency override; PRE/PROD do not need to configure it.
