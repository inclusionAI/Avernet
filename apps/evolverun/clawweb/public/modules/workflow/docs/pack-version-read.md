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

For `current`, publication identity must still match the unique active row: the
Pack ID, active tag, immutable commit candidate and canonical Pack tree path are
hard checks. If the active history content differs from the active Git workflow,
the read returns `activeHistoryMismatch: true` instead of failing. The editor
uses that flag to show the existing warning/difference/choose flow and to pick a
display baseline. The database row is never rewritten, so a historical mismatch
in `#N` cannot block deploying a valid `#N+1`.

See [the save and deployment contract](workflow-wf-save-tag-deploy.md).
