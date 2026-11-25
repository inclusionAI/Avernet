# AgentCard / GroupCard independent audit and regression matrix

## Evidence boundary

An independently delegated reviewer inspected immutable HEAD
`450df329b39bca15c42edffda06ab4722659cd55` against merge-base
`a2433cbf2bafa4c38fbff78b226d61701adcd5a2` on 2026-11-20. The reviewer used Git
objects rather than the main worker's changing files and, during the review,
did not edit source or claim local test execution. After reporting the findings,
the same agent implemented the bounded post-COMMIT fix; the main worker reviewed
that patch and ran the final local suites. This audit does
not establish absence of all defects, deployed compatibility or downstream Fuse
full-replacement behavior. Final test receipts are in [validation.md](validation.md).

## Root causes and repair boundaries

| Root cause | Repair | Regression evidence |
| --- | --- | --- |
| Admission validation differed from persistence Card projection; authentication/name helpers also performed writes | Read-only identity resolution; shared persistence projection prevalidation for URL/inline sources; delayed name reservation after owner/ref/target/Card checks; retain successful pin and concurrent-rename fences | `card_admission.rs`: public/named/update × URL/inline × domains/scopes × five malformed values; real SQLite state and actual subsequent Provider rename, plus rejection/success/rename-race cases |
| Generated Gateway publication copied only label properties, not enclosing contract semantics | Copy complete generated optional register body and required membership of all ten Group detail variants, preserving unrelated historical schema content | Gateway consistency suite compares all fourteen label locations, parent required semantics, complete register body and optional PATCH clear |
| JSON extraction failed before application-specific label error mapping | Shared structured label-path rejection mapping for Group create/PATCH, Provider Card register/configure and named registration; unrelated rejections unchanged | HTTP matrix covers seven malformed label shapes on four neighboring entries and unrelated DTO/syntax/content-type controls; existing Group POST subscription variants retained |
| Legacy explicit-ID create used upsert without existing-resource management authorization (P1, independently found) | Atomic create-only Core/repo operation. Existing IDs conflict rather than replacing anchors/members/version/type/labels. SQL unique constraint and Memory write lock fence concurrent callers; PATCH remains the update boundary | Memory/SQLite racing-create contract, type-collision preservation, application unauthorized-PATCH/duplicate-create regression and real HTTP integration |
| Group commit success was followed by a fallible response read before Core notification (P2, independently found) | Return the version-fenced committed candidate after COMMIT and invalidate cache; do not let an unrelated response SELECT suppress notification | Real SQLite post-COMMIT read-failure injection and transaction-failure rollback/zero-notification control |

The two independently found Group roots predate this PR; label writes extended
their impact. Do not attribute their initial introduction to this feature.

## Coverage matrix

- **Agent write entries:** canonical and legacy public management, named registration,
  Provider-admin registration/configure, scoped v2 registration.
- **Agent updates:** omission preserves latest labels on each CAS attempt; explicit
  objects replace/clear; supplied revisions do not retry; URL fetch occurs once.
- **Agent refresh:** manual/background and unchanged projection synchronization
  preserve labels and respect newer revisions.
- **Group:** normal creation, explicit-ID conflicts, DM reuse, Chat/DM nonempty-label
  rejection, PATCH/detail, private-to-public transition, missing-template creation,
  ordinary/eventful persistence, concurrent metadata/label changes and rollback.
- **Publication:** complete custom-plus-system maps; identity-only coalescing and
  fresh reads on retry; clear sends system-only labels; notification follows commit.
- **Contracts/bootstrap:** presence/null handling, dictionary/multiplicity, reserved
  system keys, immutable policy assembly, Gateway generated semantic equivalence.

## Cost and compatibility

Projection prevalidation is CPU-only, not a per-key DB lookup. Successful name
reservation is moved rather than duplicated; the post-fetch authorization read is
bounded and uses existing Provider facilities. Group create keeps one transaction
and its existing statement count, replacing UPSERT with INSERT. Versioned Group
mutation moves the Group/participant reads before COMMIT: the normal warm Core
query count stays unchanged, direct cold repo calls save two reads, and Delete is
unchanged. SQL timestamp/participant projection remains canonical. No database
transaction spans Card fetch or Fuse delivery.

Normal explicit-ID creation now returns conflict for an existing identity even
for a current manager; use PATCH. This closes the alternate update surface rather
than attempting a racy transaction-external permission check. DM reuse and trusted
internal upserts are not redefined. New atomic-create trait methods fail closed
for old custom implementations; downstream wrappers must delegate/implement them.
No migration, historical SQL edit, Group Triple API or deployment is added here.

Name reservation is still a separate write: a subsequent DB/CAS failure does not
imply its rollback. Returning a committed Group candidate removes a read-error
notification gap, not a process-crash window; it is not a durable catalog outbox.
Real MySQL/OceanBase fault testing, mixed binaries, deployment, rollback and Fuse
server stale-key deletion remain separate external acceptance work.


## Confirmed review-item closure register

The follow-up reconciled authenticated GitHub issue comments `7013986843`,
`7014879952`, `7031467815` and review `5989020102` against the same remote/local
baseline `450df329b39bca15c42edffda06ab4722659cd55`. There were no inline review
threads in that snapshot. **Fixed locally** below describes the uncommitted
patch, not publication, reviewer acceptance or remote thread resolution.

| ID | Confirmed issue and review source | Repair and executable regression | Closure boundary |
| --- | --- | --- | --- |
| C1 | Agent omission erased labels; `7013986843` | `public_update` merges labels from each CAS attempt; legacy configure preserves omitted intent. `source_and_visibility_omission_preserve_but_explicit_objects_replace`, `public_and_legacy_cas_retry_preserve_latest_labels_without_refetching` and bounded/supplied-revision tests | Fixed in baseline, reverified with patch |
| C2 | Legacy Group policy errors returned 500; `7014879952` | Scope the application error conversion to label InvalidRequest. `legacy_group_label_policy_errors_are_bad_requests_before_any_create_or_subscription` checks unknown key/value/multiplicity, both subscription paths, exact 400 and zero writes | Fixed in baseline, reverified with patch |
| C3 | Rejected Agent update reserved slug; `7031467815` item 1 | Read-only authentication, validate labels/target/Card before pin, pin once per CAS request. `rejected_label_updates_leave_provider_slug_mutable` checks persisted config and actual subsequent rename; positive pin and fetch/rename race tests remain | Fixed in baseline and expanded locally |
| C4 | Gateway Group PATCH schema lacked labels; `7031467815` item 2 | Published schema includes optional replacement/clear; generated-schema test asserts exact definition, optionality and `{}` support | Fixed in baseline and expanded locally |
| C5 | Legacy Group label JSON errors were plain-text 422; `7031467815` item 3 | Structured serde-path matching maps only label type errors to 400 with exact field/reason. `legacy_group_label_type_errors_are_structured_before_any_write` plus neighboring-entry matrix and unrelated-status controls | Fixed in baseline and expanded locally |
| C6 | PR description still claimed omission clears; `7031467815` item 4 | Read the current PR body: Agent input table and compatibility section both state omission preserves and explicit `{}` clears | Already corrected remotely; no remote write needed in this follow-up |
| C7 | Invalid Card projection pinned names on public register/named/update; `5989020102` item 1 | Use the persistence projection validator before pin. `projection_rejections_never_pin_or_mutate_across_card_entrypoints` executes 60 cases and real subsequent renames; valid/rename-race controls retained | Fixed locally, not pushed |
| C8 | Gateway register operation lacked complete request body; `5989020102` item 2 | Copy the complete generated optional requestBody, not just the label property. `test_register_declares_the_complete_generated_optional_request_body` checks complete equality | Fixed locally, not pushed |
| C9 | Ten response variants omitted labels from required; `5989020102` item 3 | Synchronize all ten response required lists. Tests compare all 14 label locations and distinguish required responses from optional requests | Fixed locally, not pushed |
| G1 | Explicit-ID create could overwrite an unauthorized Group; independent audit | Atomic create-only Memory/SQL contracts, application Conflict assertion and real HTTP 403/409/detail-preservation test. New Core tests assert omitted/clear/replacement replays conflict without notification, preserve DB and cache independently, and rollback all creation rows on failure before successful retry | Fixed locally, not pushed |
| G2 | Read failure after Group COMMIT suppressed catalog notification; independent audit | Precommit canonical reads and postcommit candidate. Six fault/projection tests cover commit-notify, rollback/no-notify, stale-cache retry, plus Group status, routing, Bot/Human membership, participant mode and both scope-update forms against fresh SQL reads | Fixed locally, not pushed |

The projection comparison now exercises ten successive non-delete mutations,
not only label PATCH. Both the original SQL state and the original input cache
are compared to their own preimages on rejected create; display-only cached
names/timestamps are intentionally not mistaken for SQL canonical fields.
The follow-up strengthens error assertions from generic `is_err()` to explicit
Conflict at application and shared Memory/SQLite boundaries.

Publication and coverage gaps are intentionally not marked as fixed review items:
full-workspace/LLVM/Singlebox, overall architecture, real MySQL/OceanBase and
remote CI on this patch remain separate evidence requirements in validation.md.
