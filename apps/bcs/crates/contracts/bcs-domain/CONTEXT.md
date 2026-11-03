# bcs-domain Context

## Caller identity boundary

`caller_identity_material` owns runtime-only trusted caller envelopes shared by Service API and Caller Identity Plugin API. These data types contain no signing, exchange or provider selection behavior.

## Compatibility

Caller subject reference tags are runtime-only Rust types, not serialized wire or database values. The provider-specific tags are now `PluginLineageRef` and `ExternalSubjectRef`. Consumers and deployment-specific plugins must rebuild against these names together; no wire or database migration is required.
