# Pack version Git reads

`draft` reads only `wf/<workflowId>`. `current` reads the tag named by the
unique active deployment-history row, never the latest wf commit or greatest
tag number. No wf means no saved draft; it does not prevent reading an
existing active production tag. The response reports `origin/wf/<id>` or
`refs/tags/deploy/<id>/#N`. For a draft, `baseCommit === draftHead`; for
production, `draftHead` independently reports the observed wf write lease.

Only wf and the explicitly bound production tag are fetched, shallowly.
There is no saved/ lookup, comparison, fallback, synchronization or migration.
Changes to any unrelated branch do not invalidate the read. The selected refs
and active DB identity are rechecked before return.

For `current`, the bound active deployment row must be consistent with the
immutable Git projection. Legacy rows that omit only the authored top-level
workflow `version` are read by borrowing that exact value from the active Git
YAML only when every other field matches; the database row is not rewritten, so
the existing history-difference warning remains visible. Any other mismatch
continues to fail closed.

See [the save and deployment contract](workflow-wf-save-tag-deploy.md).
