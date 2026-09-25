# Skill package storage

`createClawevolveModule` accepts an optional `skillPackageStorage` host binding:

```typescript
{ bucket, prefix, store, urlStore? }
```

`store` implements the existing `ObjectStore` contract. `urlStore`, when supplied,
is its signing counterpart with an endpoint reachable from the executing Bot.
The host owns configuration and credentials. No credentials enter task inputs.

The binding covers uploaded Stage implementations, registered Skill versions,
frozen task baselines/candidates, and constructed Stage test fixture ZIPs. New
references include the configured bucket and prefix; the logical paths
(`skills/`, `stage-implementations/`, `stage-tests/`) follow
that prefix. Both upload and signing use the same reference. Business Skill
execution and the container download protocol are unchanged.

When omitted, the module uses its existing artifact store and bucket. When supplied,
new writes use the new binding. Persisted references in the previous artifact bucket
remain readable and in-flight candidate PUT URLs continue using that store. This
is explicit reference routing, not a retry against a different bucket after an error.
Only configured locations may be read or signed. There is no automatic data migration.

Persisted references with the previous `evolve/` logical-path prefix remain readable
and signable at their exact original object keys, including candidate PUT URLs.
Frozen fixture validation accepts either layout while still checking the task and
fixture identity. Explicit legacy keys supplied by existing storage callers also
remain supported. All new platform-generated paths omit that redundant layer;
no existing object is renamed.

Native Pack files, launch parameters, log archives, and feedback attachments retain
the existing artifact storage. Deployments must keep the old store available while
old Skill versions/tasks reference it. Reverting the host configuration does not
move packages back; keep the dedicated binding when rolling back other changes.

Validation covers dedicated and legacy storage routing, malformed references,
registration/editing across stores, candidate preparation, and module-level Stage
upload → persisted reference → hardening Step signed input. External credentials,
OSS permissions and Bot network reachability require deployment acceptance.
