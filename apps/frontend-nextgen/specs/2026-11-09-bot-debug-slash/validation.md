# Validation checkpoint

Updated: 2026-11-10. Personal-Bot live preproduction checks passed; service-Bot draft-versus-online acceptance is **not complete**. The user has reauthorized an Avernet dev PR after review; delivery is pending. No deployed-product change is claimed.

## Baselines and scope
- Public source started at `df0411cfcece160549446e5ed30a70b699a59edc`.
- Refreshed public dev on 2026-11-10: `9c4523596fe469accee84764347e409cf58d701c`; its frontend tree is identical to the starting baseline.
- Internal assembly snapshot: `0df42ef512770bebe834b6952e3867fedc76f708`. Refreshed internal dev to `a23fbda81bf16afd7b89223f38e6e612351d2112`; `git diff` confirms the complete internal frontend subtree is unchanged, so the assembled source remains current for this feature.
- No backend, database, SDK, dependency-version, knowledge authorization or deployed-service changes.

## Automated evidence
- Public dependencies were installed from the committed public lockfile using the public registry, without changing dependency versions. Public focused regression: 10 suites / 48 tests passed. Real Sender covers root `/` navigation, keyboard command selection, file-only messages, uploads, stop/model controls and extension wiring.
- Shared command tests cover lazy refresh, scope-change stale-response rejection, retries and unavailable identity.
- Public `npm run setup` and Open Core artifact checks passed. The complete public `npm run ci` passed: structural guardrails, typecheck, lint, all 741 suites / 6,061 tests, and production Webpack build. The focused regression was also rerun separately (10 suites / 48 tests).
- Internal assembled regression: 5 suites / 37 tests passed. This includes actual KnowledgeConversationComposer + actual Sender + debug input, mixed file/knowledge submission, source-owner addressing and verify-stage lookup.
- Internal knowledge APIs and resource APIs in these tests are mocked. These results are **not** evidence of live retrieval or backend connectivity.
- Internal assembled full typecheck remains non-green in two untouched tests: `AuthorizationPanel.test.tsx` (obsolete auth response field) and `channelBindingService.test.ts` (missing groupContextDelivery). No unrelated fixes were made.

## Assembly, authentication diagnosis, and live acceptance
The internal snapshot was assembled with the public working copy through `assemble-internal.sh --dev --open-dir "$AVERNET_HOME/apps/frontend-nextgen"`, using the preproduction preset. The local development bundle compiled and HMR rebuilt the changed source. The internal-mode process serves port 8000, with matching `root-master` HTML and client entry.

### Local authentication resolved (2026-11-10)
The requested hosts-mapped HTTP entry served the page but could not authenticate business requests. A preserved normal workbench request returned HTTP 401 with `code=40100`, `message="Authentication is required"`, and `error_code="unauthenticated"`; a separate legacy initialization request returned `missing login cookie`. A direct API-navigation experiment returned a different X-One-ID error and is not treated as the workbench request's root cause.

The relevant identity Cookie is `IAM_TOKEN`, not the previously inspected `ALIPAYJSESSIONID`. Read-only browser metadata showed `IAM_TOKEN` is Secure and HttpOnly. The actual local HTTP workbench request omitted it. Checking the non-Secure legacy session Cookie had therefore not ruled out protocol-related credential exclusion.

A disposable local HTTPS override now uses a leaf certificate for the hosts-mapped development hostname, signed by the machine's already trusted local development CA. Certificate verification and an HTTPS health request passed without disabling TLS verification. No root trust changes, security-warning bypass, Cookie copying, fake identity, product authentication change, or backend change was made. After switching to HTTPS, the browser authenticated and loaded the real user's Bot list; the login loop stopped.

An earlier preproduction master-site development-entry experiment returned `Only Accept GET Request` and was reverted. Temporary proxy diagnostics were also removed. Local TLS paths and runtime configuration remain outside this public change.

### Observed live browser results
- Bot editor debug input displays shared root `/` entries for file, skill, clear and the internal knowledge extension.
- File selection renders the empty state for sessions with no uploads. With explicit approval, a 112-byte plain-text fixture containing only a regression marker was uploaded to the isolated debug session. A recorded upload intent returned HTTP 201 and upload completion produced a real file chip. Removing the chip cleared only the unsent reference; reopening `/file` fetched the file candidates from preproduction and selecting one reinserted the chip without sending. Two same-name harmless fixture copies were visible after upload/retry; no server-side deletion was attempted. The UI enabled file-only submission; actual file-only transport is covered by automated tests, not a separate live message.
- Skill candidates load from preproduction. Keyboard selection inserts a skill token without sending a message or invoking the skill.
- A Bot without enabled knowledge renders the empty state; an existing authorized test Bot renders its enabled knowledge candidate. Selecting it adds the real extension's reference chip without sending.
- The plus menu retains upload and knowledge entries. Its knowledge entry opens the same command flow.
- Creating a separate debug session resets the draft and pending knowledge references; no old chat was cleared and no Bot configuration was saved.
- Fullscreen and restored editor layouts retain the functional input and message display.
- Ordinary chat loaded the same newly created session and its persisted request/reply, retained the four root commands, and loaded real skill candidates through the shared hook. Its unsent test draft was cleared.
- A harmless message with an actual knowledge reference completed a preproduction roundtrip and the Bot returned the requested `SLASH-KNOWLEDGE-OK` marker. The prompt explicitly prohibited tool calls or file/configuration changes. This verifies reference decoration and chat transport, **not knowledge retrieval quality**.
- A file chip selected through `/file`, a real knowledge reference and a harmless text prompt were sent together. The editor displayed the attachment and decorated prompt, and the Bot returned `SLASH-MIXED-OK`. This proves mixed-reference chat transport and response, not file parsing or knowledge retrieval quality.

Destructive `/clear`, live service-Bot verify/online stage switching, and a separate file-only message were not exercised live. Command dispatch, stage/owner arguments and file-only payload behavior are covered by the automated tests above. Ordinary-chat command and persisted-session regression passed as described above. No Bot configuration or authorization was changed.

## Internal test ownership
Only the disposable local engineering snapshot was amended to sync these public suites into the assembled tree:
- `test/components/BotWorkshop/DebugChatComposer.test.tsx`
- `test/services/botWorkshop/debugChatFiles.test.ts`
- `test/pages/Workspace/hooks/useBotSessionFileCommand.test.tsx`
- `test/pages/Workspace/hooks/useBotSessionCommands.test.tsx`

The extra actual-knowledge/debug integration suite also lives only in that disposable internal snapshot. No internal branch or test-ownership change has been committed or proposed. When consuming the public change, align the internal public-test manifest rather than running stale copies against the new internal component signature.

## Service editor stage regression (2026-11-10)
The earlier live checks used personal Bots, which always resolve to draft. They did not establish the service-Bot editing requirement. A new test mounts the actual detail page and debug panel, mocking request hooks and the leaf composer only. Before the fix, both a running service Bot opened with `type=edit&runtime_stage=online` and one without an explicit stage incorrectly passed `online` to debug requests and the composer: 2 failed, 6 passed. The detail page now chooses draft for editing, preserves explicit/lifecycle verification as verify, and preserves ordinary view stages. After the fix all 8 cases pass. The debug remount key uses the same resolved stage, keeping session/composer state isolated.

Actual internal Sender + knowledge extension tests additionally cover draft/current, verify/last_recorded, online/last_recorded, distinct stage candidates, and rejection of an online-recorded response when draft/current was requested. The assembled stage and knowledge regression now passes 6 suites / 49 tests. APIs are mocked: this establishes frontend wiring, not live backend selection correctness.

The changed detail source has been copied into the disposable assembled frontend and HMR compiled successfully. A live service Bot with differing draft/online enablement has **not** been exercised. No knowledge enablement, authorization, publication or edit-lock takeover was performed. The full public CI result above predates this stage fix; post-fix targeted checks are recorded separately, not presented as a new full-CI run.


## Live service-Bot slash diagnosis (2026-11-10)
The local service-Bot editing route with an explicit draft stage initially displayed “暂无调试会话”; the composer was disabled and the session-dependent knowledge extension was not mounted. This is a session prerequisite, not a service-type exclusion. Creating one empty “Bot 工坊调试” session enabled the input and established a connection. Typing `/` then displayed skill, file, clear and knowledge entries. Opening file completed with the expected empty-session message “暂无可引用文件,请先上传”. Opening knowledge displayed an actual enabled test knowledge-base candidate. No chat message was sent, no file was uploaded, and no enablement, authorization, publication or edit-lock takeover was performed by this diagnosis.

The focused assembled regression was rerun: 3 suites / 22 tests passed. This live check establishes service-draft command availability after session creation; it does not establish a differing online selection, file transport or knowledge retrieval. The empty-session UI can misleadingly appear to lack slash support because its disabled placeholder still advertises commands; improving that guidance is a separate, small UI follow-up rather than relaxing session/identity requirements.

## Delivery gate
The user reauthorized review followed by an Avernet dev PR on 2026-11-10. Review and refreshed baseline validation are complete; submission remains pending. The branch was successfully rebased onto refreshed dev using the fetch mirror for lazy blob retrieval, without changing the authoritative push target. Live service-Bot command availability and current draft enablement matching were verified; differing online selection and service-Bot file/mixed-send transport remain untested live and must be disclosed in the PR. The generic knowledge-reference presentation seam was subsequently approved and added. Removal of the internal authorization badge and empty-session guidance remain excluded follow-ups.


## Final paired implementation and review (2026-11-10)
- The user additionally authorized the OCB implementation and a second OCB → dev PR. Public source only owns `UserMessagePresenter`, its pass-through default and the shared display Host. OCB owns strict template parsing, read-only knowledge cards, registration and internal integration tests.
- Both frontend subtrees remain unchanged by their latest dev refresh: public baseline `9c4523596fe469accee84764347e409cf58d701c`; internal baseline `49b24a36e178e622fcf9eea3ea5dcefee63b3f0a`. Both feature branches were rebased successfully; no unrelated dirty checkout was changed.
- After the stage and presentation changes plus review fix, complete public `npm run ci` passed: guardrails, typecheck, lint, **744 suites / 6,081 tests** and production Webpack build. Open Core artifact checks also passed. Subsequent rebases have byte-identical frontend source and tests.
- The isolated latest two-repository assembly passed internal lint, production Bigfish build and **39 suites / 324 tests**, including all internal knowledge suites, real Sender integration, strict parser/fallback, mixed file references, safe special-character cards, original copy/edit input, stage routing and debug clear handling. The public-test assembly script's own regression passed.
- Internal full typecheck after framework setup still reports only two unchanged baseline test diagnostics: obsolete auth response `type` in AuthorizationPanel and missing `groupContextDelivery` in channelBindingService. It is not reported as a passing full gate. The initial fresh-tree check before generated Umi setup was invalid and was rerun after setup.
- Standards review found one new clear-operation layering issue; it was moved into the scoped `useDebugChatClearContext` Hook and tested. Incremental review closed it. Spec review found no confirmed implementation defect or scope creep. Performance review found no actionable request amplification: no idle fetches; file queries occur per explicit submenu entry, not per typed character; presentation is synchronous and zero-I/O. Rapid re-entry overlap and large candidate lists were not benchmarked.
- OCB's existing gitlink is unchanged. Its PR must explicitly depend on the public PR; final gitlink integration must use the actual public merge commit after mirror reachability verification. Neither PR is automatically merged, and no remote deployment is authorized.
