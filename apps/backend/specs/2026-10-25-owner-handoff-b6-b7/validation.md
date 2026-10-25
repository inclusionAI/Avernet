# B6–B7 validation

## B6

- Backend community suite: **25,352 passed, 40 skipped**, 488.65 seconds.
- Full-source coverage: 90%; changed executable lines: **1,824 / 2,495 = 73.11%**.
- Legacy contract inventory pins 42 Skills and 22 SkillSets routes: paths, methods, response models and order.
- Persisted owner!=entity cases verify stable resource paths, actual Owner authorization and Render Screen personal-resource boundaries.

## B7

- Real ORM journal/aggregate tests cover default/independent execution, repeat A→B→C transfer, resource identity, collaborator policy, work-order blocking, uncertain remote outcomes, pending consent, generation drift, empty runtime acknowledgements, partial failures, transaction rollback, leases, enqueue gaps and scoped recovery.
- Every new command has an HTTP happy/error case driven by the real core coordinator and persisted journal with explicit external contract fixtures. Closed-default HTTP flow also checks existing Bot reads.
- Route-unit test split retains **all 170 original tests**, with no removals or changes to CI exemptions.
- Focused compatibility repair run: **261 passed**.
- Backend coverage run: **25,406 passed, 40 skipped** and one retry-HTTP failure, which exposed use of a public projection as an enqueue scope. Fixed by reloading the private scoped record; added a direct regression and reran final checks below. Full-source coverage was **90%**; changed executable lines: **572 / 620 = 92.26%**.
- OCB corporate Passport/caller-credential focused suite: **116 passed**. The starting OCB dev lacked B5 addressing; its separate B5 alignment commit retains existing tests.
- tcauthmng Maven reactor: **22 tests passed**, BUILD SUCCESS. Includes ALC/AAC generation fencing when a new ALC has no corresponding AAC, correlated admin replacement, recovered consent metadata and retirement failure boundaries.
- Undefined-name checks, changed-Python SAST and whitespace checks pass; every touched/new Backend Python file remains below 1,000 lines.

Final Backend full rerun: **25,408 passed, 40 skipped**, 379.58 seconds.
All five new handoff commands' ten HTTP happy/error cases pass. The committed
change-range SAST scan passes. One intermediate all-suite run hit a Swarm creation
E2E failure; its related nine tests passed in isolation and the final unchanged
Backend code passed the complete suite. No Swarm test, skip or threshold was
changed to obtain that result.

Local companion commits: OCB B5 alignment `9d7aad94e0`, OCB B7 adapter
`1d4629c672`, tcauthmng B7 `a33faa5`. Backend B6 is `6f837537d`; B7 is the
separate next commit on the same branch. Nothing was pushed or deployed.

## Limits of this evidence

No production SQL, real AgentPass/AAC, physical runtime, long-lived session, Frontend or SecBaaS calls were performed. The full live Singlebox stack was not run; the existing Bot-management live gate is pending in the repository module manifest, while its standalone legacy fixture also cleans user workspace paths. The default-closed flow was verified in-process. No gate was waived or threshold reduced for this change. Full corporate CI was not run in the shared community environment, which lacks corporate runtime SDKs; focused RPC tests explicitly use the matching working Backend source.

Keep production initiation disabled. Apply the new journal DDL and deploy/pin compatible tcauthmng/OCB/Backend before controlled environment acceptance. Real revocation idempotency, old ALC→AAC/AEC invalidation, every runtime target, background mutators and long-lived sessions remain release prerequisites in rollout.md.
