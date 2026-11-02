# Pack version Git reads

Explicit GET pack-version reads discover only the workflow's `wf` and `saved`
refs and fetch existing refs with `--depth=1` and without tags. The Git remote
must support shallow fetch over a compatible smart transport (for example,
smart HTTP, SSH, or a compatible local/file transport) for this read path. The reader
verifies both refs again before returning a snapshot and fails closed if they
change.

Dumb HTTP and any other transport or server that cannot serve shallow fetches
are not supported by this read path: the request fails closed rather than
retrying without a depth limit or cloning full history. Other fetch errors are
also not retried. Save and deploy continue to use their existing full-clone
path; this compatibility boundary applies only to explicit pack-version reads.
