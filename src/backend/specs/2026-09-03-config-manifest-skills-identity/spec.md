# Delivering the Two Fetching Categories: `skills` and `identity` Materialisers

## Summary

A manifest declaring `skills` or `identity` is accepted today and then applies
nothing: the engine's registry holds only the two fetch-free materialisers
(`mcp`, `script`), so both categories take the "no materialiser" path — every
entry fails with a readable reason. This work item registers the first two
**fetch-consuming** materialisers and the one entry-fetch pipeline they run
on, so the feature's driving scenario — *pull content from the caller's own
servers, then install it* — exists for the two categories business asked for
first.

This is W5 (#1473) of `docs/bot-config-manifest/work-items.zh-CN.md`. It is
the wave where W2's guarded transport, W3's named credentials, and W11's
platform-side content store meet, through one funnel: substitute
`${BOT_*}`, consult the platform's own newest receipt for the source, fetch
under the declared credential, file the result back with the store.

## Motivation

**The accepted-but-inert window is exactly what W1's rule forbids, and W5 is
where it closes for these two categories.** "This surface never accepts
something it cannot apply" — the capability table marks `skills` and
`identity` supported (the categories were planned for this wave), and until
this lands a `PUT` carrying them stores a document whose every entry then
fails at apply. Closing the window is *registering materialisers*, not
deleting branches: the sparse-registry design means W5's engine changes are a
tuple and five constructor arguments.

**The fetch policy had to be decided once, and decided here.** Pinned versus
unpinned is a declared-digest question, and it decides store-first versus
fetch-first: content addressing makes an in-store receipt that matches a
declaration *the declared bytes*, so a pinned entry with a matching receipt is
served from the platform's copy — a re-apply of a converged document fetches
nothing and writes nothing. An unpinned entry re-fetches every apply so it
converges to the source; `keep_last` exists for the day that fetch fails, and
reads the latest receipt only when it may — a receipt disagreeing with a
declared digest is stale, not "last".

**Parity with the manual paths is a property, not a goal.** A manifest skill
is indistinguishable from an uploaded one *because it is one*: the fetched
zip is validated by the same `SkillPackageValidator` the raw-zip router path
uses and handed to `upload_local_skill` as its canonical package. Identity
entries address the same coordinates the identity router uses
(`identity_coords_from_record` — resolved in core for exactly this consumer)
and write through `update_bot_file`. Two write paths for one area is how
drift starts; this wave refuses to start it.

## Scope

**In:** the entry-fetch pipeline (`apply/entry_fetch.py`); the
`IdentityMaterialiser` (file set minus reserved names, empty-write removals,
legality re-asked per the bot's engine); the `SkillsMaterialiser` (active-set
area, Set-governance narrowing, upload-road packages, front-matter/entry name
agreement); the W11 `latest_receipt` lookup the pipeline consults; the
registry wiring and its DI (`ManifestFetchModule`, the narrow identity port
that keys the lazy provider); the `content`-on-skills PUT refusal (a skill is
a package, inline text cannot be one); the module README and this spec set.

**Out:** `resources` (W6 — its area is declared-path subtrees and needs its
own materialiser); named and git sources (W7; the validator already refuses
`from`/git forms); apply-on-lifecycle (W8 — apply remains
`POST …/apply`/dry-run only); anything teclaw-specific in delivery (the
artifact path is W8's concern, and skills installed through the upload path
reach teclaw the way manual ones do — through the activation and projection
machinery that already exists).
