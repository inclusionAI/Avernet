# Independent internal collab export implementation plan

> Execute after `plan.md` Tasks 1–16. This is the required second delivery
> phase of `spec.md`, not an optional follow-up. Keep every checkbox pending
> until its implementation and validation have actually run.

**Goal:** Keep Avernet's complete nextgen product runnable through Singlebox,
while producing an independent internal collaboration-site source artifact
using only the nextgen public source and private nextgen overlay.

**Ownership:** Shared source and pure contracts live in Avernet. Internal
auth/branding/configuration and the deployment exporter live in OCB's
`apps/frontend-nextgen`. No private implementation or registry setting is
added to Avernet. The exporter is an internal deployment tool, not a replacement
for an OSS exporter. Historical `apps/frontend` export scripts are not inputs.
Private composition/configuration stays inside the existing `src/internal/`
and `config/internal/` boundaries, so the established public exclusion rules
also cover the new private entry and templates.

**Evidence behind the design:** OCB's `assemble-internal.sh` copies the public
nextgen `src`, routes and A2A package; `extensions/internal.ts` imports
`extensions/empty.ts`. Thus exporting the current internal entry would include
the full task assembly, and shrinking Avernet itself would remove shared source
needed by the complete internal frontend.

## Output and invariants

```text
Avernet/apps/frontend-nextgen (complete public source, unchanged by export)
  + OCB/apps/frontend-nextgen (private nextgen overlay)
  -> disposable internal assembly
  -> dedicated collab-internal configuration and source closure
  -> NEW output root/
       COLLAB_EXPORT_MANIFEST.json
       apps/frontend-nextgen/{src,config,test,public,deploy,scripts,...}
       packages/a2a-chat-client/
```

Only the output tree is sliced. Neither source repository is cleaned, replaced,
or rewritten. Exporting twice requires two distinct empty destinations; a
failed run may remove only the staging directory it created itself. The
exporter does not publish artifacts, update submodule pointers, or change the
user's current branch.

The package inventory may retain unused installed dependencies from the locked
open nextgen package pair (revised from "private nextgen package pair" —
see spec.md: the slice has no private-registry-only dependency, so the public
lock ships and the internal registry is environment-injected). Source
exclusion, route exclusion and runtime API
exclusion are mandatory; npm dependency minimization is not a new requirement.

### Task 17: Make source slicing type-complete

**Avernet files:**
- Modify: `src/extensions/contract.ts` from Task 3.
- Create: `src/extensions/contracts/avatar.ts`, `groupTasks.ts`,
  `groupRouting.ts`, `taskPanelBootstrap.ts`.
- Modify: the corresponding existing task/avatar components, hooks and services
  to consume the shared contracts; update their Open registrations.
- Modify: `scripts/check-collab-closure.cjs` and its fixture suite from Task 14.
- Add: `test/extensions/featureContracts.test.ts` for real-implementation
  assignability and consumer-compatible inputs/outputs.

- [ ] **Step 1: Separate runtime and source dependency edges**

Task 14 correctly erases type-only edges for its runtime bundle check. The
export file list must also follow type-only imports/re-exports, referenced
ambient declarations, local package sources and assets. Expose the parsed
edge kind from a reusable parser instead of changing runtime rules to include
types indiscriminately:

```ts
type LocalEdgeKind = 'runtime' | 'type' | 'asset';
interface LocalEdge {
  specifier: string;
  kind: LocalEdgeKind;
}
```

Keep runtime checking and source inclusion separate consumers of the same
resolution rules. A file needed only for types must still exist in the exported
source tree; skipping it because the JavaScript erases it is a failure.

- [ ] **Step 2: Replace implementation-derived slot types**

The initial Task 3 `typeof` imports are valid in the full tree, but are an
intermediate implementation only. Replace every dependency from the shared
slot contract to an omitted implementation with a pure structural contract:

| Current type source | Shared contract to extract |
| --- | --- |
| `BotAvatar/AvatarEditor` | Avatar editor props and component type |
| `useGroupTaskExecution`, composer menu, task-linked session, task mapper | Input/output, component props and function contracts in `contracts/groupTasks.ts` |
| `groupExecuteService.createGroupViaExecute` | The existing three-argument executor signature and domain result |
| `useServiceBotRouting`, `ServiceBotRoutingControl` | Routing model, hook signature and control props |
| `useSessionTaskPanelBootstrap` | Bootstrap argument type and `void` return |

Move the existing definitions rather than inventing permissive `any`, `unknown`
or optional fields. These contract files may reference pure domain models,
React types and SDK types, but not implementations in `pages`, task hooks,
TaskPanel assets or BotWorkshop components. Annotate the real implementations
and Open registrations against the extracted types so signature drift fails
typecheck. Inspect the entire type closure, including capability, navigation
and composer type imports; extract any further forbidden implementation-owned
types by the same rule.

- [ ] **Step 3: Verify an actually incomplete source fixture**

Create an export fixture containing a retained component and a pure slot
contract, with the corresponding task/avatar implementation intentionally
absent. Typecheck that fixture. Add the original implementation-derived
`import type` back in a negative fixture and assert either export validation
or typecheck fails. Run the full Open typecheck and affected component/hook
tests too; do not weaken the full-source `tsconfig` to hide failures.

### Task 18: Add the private nextgen collab assembly

**OCB files, relative to `apps/frontend-nextgen`:**
- Create: `src/internal/collab/extension.ts`.
- Create: `src/internal/collab/capabilities.ts` and
  `config/internal/collab/{config.ts,config.local.ts}`.
- Modify: `src/extensions/internal.ts` only to extract reusable private leaf
  auth/identity/brand definitions; its full-product behavior remains unchanged.
- Modify: `scripts/assemble-internal.sh` to pass through the public collab
  route file as well as the existing full route baseline.
- Add: `test/extensions/collabInternal.test.ts` and targeted private auth tests.

- [ ] **Step 1: Build a dedicated private composition root**

Import the public `extensions/collab` implementation directly and extend only
the required private auth, identity and branding capabilities. Use selected
private leaf adapters from `src/internal/collab/capabilities.ts`. Never import
the complete `internal.ts`, which imports `empty.ts` and task panel registration.
Do not reuse the full private navigation, gray-release old-site link, search,
notification, admin or task feature overrides.

The resulting assembly must retain null task/routing/bootstrap slots,
`avatarEditor: null`, exactly three navigation entries (协作群/发现/外部 Bot),
BCS-only public Bot/group discovery with experts and Human private chat disabled,
and disabled spaces,
search and notification initialization. It uses the existing private auth
strategy and identity mapping; it must not silently fall back to public OAuth.
Keep shared group/BCS/external-Bot logic in Avernet. Select any additional
private side-panel adapter explicitly and subject its full closure to the
same exclusion checks before enabling it.

- [ ] **Step 2: Fix exported config to the selected form**

The private config template imports only the collab routes and maps the exact
`@/extensions$` alias to `src/internal/collab/extension.ts`. It does not probe for
`routes.internal.ts`, merge the full route tables, or import the full private
runtime config (which registers unrelated proxies and microfrontends).
Reuse only the existing private build/bootstrap pieces needed for the selected
authentication and collaboration runtime. Keep internal addresses and registry
settings entirely in OCB configuration/environment.

For the exported tree, generate `src/extensions/index.ts` to re-export
`../internal/collab/extension`, and align the Umi, TypeScript and Jest mappings with that
entry. Webpack's exact alias alone is insufficient: TypeScript otherwise finds
the full-tree facade and tries to import the absent `empty.ts`.

- [ ] **Step 3: Verify required and forbidden behaviors**

Run full internal-assembly typecheck and affected existing suites after the
shared contract changes. Separately load the collab-internal entry and assert
private login/identity behavior plus all collab restrictions. A test importing
`internal.ts` or `empty.ts` indirectly must fail the closure check. Verify no
legacy frontend implementation or old-site escape link is required.

### Task 19: Export to a new standalone directory

**OCB files, relative to `apps/frontend-nextgen`:**
- Create: `scripts/export-collab.sh` (staging orchestration).
- Create: `scripts/collab-export/{graph.cjs,emit.cjs,manifest.cjs}`.
- Create: `scripts/collab-export/{graph,emit}.test.cjs`.
- Create: `scripts/collab-export-tests.list` and
  `test/setup/registerCollabExtensionSlots.ts`.
- Create: `deploy/collab/{Dockerfile,README.md}`.
- Modify: `package.json` to expose `export:collab` and the export fixture gate.

- [ ] **Step 1: Define the deployment-export command**

```bash
# Run from OCB/apps/frontend-nextgen; output must be new and outside both repos.
npm run export:collab -- --out /tmp/nextgen-collab-site
```

The wrapper resolves the configured Avernet submodule source, records its
revision plus the OCB revision, and invokes the existing nextgen
`assemble-internal.sh` into a unique private staging directory. Use neither
`--install` nor `--save-lock` during assembly: those can write the authoritative
private lock back into OCB. Dependency installation, if needed for graph
resolution, occurs only inside disposable staging/output directories. Use
`--open-dir` for an explicit public source override with the same validation.

Reject output/source overlap after resolving symlinks, an output equal to or
above either repo, a nonempty destination, path traversal, and unresolved local
dependencies. Never run `git restore`, `git clean`, or legacy export commands.
Validation failure returns nonzero and must not report a completed artifact.

- [ ] **Step 2: Compute and emit the source closure**

Seed the graph from the fixed collab route components, app/layout/bootstrap,
private collab assembly and declared framework entries. Follow static imports,
re-exports, literal dynamic imports/require, type edges and local package edges.
Include styles and their relative imports/URLs, imported assets, ambient
declarations and explicitly declared public resources such as runtime config,
brand images and favicon. Do not infer framework/string-referenced resources
solely from TypeScript imports. Missing local files and opaque bundled loads
are errors, never silent exclusions.

Map staging's flat A2A package into output `packages/a2a-chat-client`; map the
application to output `apps/frontend-nextgen`. Generate all alias/config files
for that output geometry and copy the nextgen package/lock pair, selected build
tooling, deployment files and public assets. Keep the locked dependency versions;
if exported scripts are reduced, update only the script surface and validate
the lock with a clean install. Do not copy `.umi*`, `node_modules`, `.git`,
runtime logs or environment files. Resolve assets into regular files; no emitted
symlink may escape the output root.

Reject excluded task/BotWorkshop/other-page implementations even when reached
only through types or tests. This is stricter than the runtime guard. The
internal private-path allowlist is specific to collab leaf adapters, not all
of `src/internal`. Keep the public Open Core guard unchanged.

Select artifact tests through `collab-export-tests.list` and copy their helpers
transitively. Their setup registers only collab slots; it must not use the
full-tree setup that imports real task implementations. Run the full Open and
internal regression suites in their respective full trees, not in the slice.

- [ ] **Step 3: Write a reproducible manifest**

`COLLAB_EXPORT_MANIFEST.json` includes:

```json
{
  "schemaVersion": 1,
  "form": "collab",
  "audience": "internal",
  "sources": {
    "avernet": { "revision": "<resolved git SHA>", "dirty": false },
    "ocb": { "revision": "<resolved git SHA>", "dirty": false }
  },
  "assembly": "apps/frontend-nextgen/src/internal/collab/extension.ts",
  "files": [{ "path": "<emitted relative path>", "sha256": "<content hash>" }]
}
```

The placeholders above describe computed fields, not values to emit. Sort
paths, hash generated files too, exclude the manifest's own recursive hash,
and record dirty input state accurately. Include no absolute machine paths,
cookies, auth headers or environment secrets. The same source bytes and export
settings must produce identical file/hash lists. Check both source inventories
before/after export, excluding pre-existing runtime/cache directories.

- [ ] **Step 4: Test export failures and source preservation**

Fixture cases must cover a lazy forbidden import, a type-only import of an
excluded implementation, an allowed pure contract, an asset/CSS dependency,
the local A2A package, alias resolution, an unresolved local file, an escaping
symlink, nonempty/overlapping outputs and an accidental legacy path reference.
Assert source hashes are unchanged on success and every failure. Repeat export
to two empty directories and compare manifest file/hash lists.

### Task 20: Validate full Singlebox and isolated deployment separately

**Avernet files:**
- Update: `docs/singlebox-nextgen-local.md`,
  `apps/frontend-nextgen/docs/deployment.md`, `apps/frontend-nextgen/CLAUDE.md`.
- Extend existing tests if needed: `singlebox/verity/test_singlebox_frontend_variant.sh`
  and `test_singlebox_variant_gate.sh`.
- Keep `.github/workflows/frontend-nextgen.yml` running full Open and public
  collab builds and shared contract/closure tests.

**OCB files:**
- Add: `scripts/ci-collab-export.sh` under `apps/frontend-nextgen` and invoke it
  from the actual private nextgen CI entry without replacing full-form gates.
- Document isolated image build and private deployment environment in
  `apps/frontend-nextgen/deploy/collab/README.md`.

- [ ] **Step 1: Verify the complete public source**

From Avernet root, run:

```bash
bash singlebox/verity/test_singlebox_frontend_variant.sh
bash singlebox/verity/test_singlebox_variant_gate.sh
FRONTEND_VARIANT=nextgen FRONTEND_FORM=open bash singlebox/singlebox.sh start all
```

Record the actual frontend source path, `dev:local` command, frontend readiness
and Gateway readiness. Smoke login, a collaboration flow, and a pre-existing
non-collaboration route such as `/bot-workshop`. Exercise supported local flows;
do not confuse absent optional remote services with a frontend bundle failure.
Run full public CI before/after the source-contract changes and confirm export
does not alter full public routes or source inventory. Do not run the legacy
`bcs_frontend` composite for this verification.

Repeat the nextgen startup check in a disposable verification checkout/image
that omits only the legacy `apps/frontend` implementation directory while
retaining the Singlebox launcher and required backend modules. Do not delete
the user's real legacy directory to run this check. This demonstrates that
the complete public nextgen startup has no accidental legacy dependency.

- [ ] **Step 2: Build the export with no access to its sources**

Move/copy the output to an isolated job/container. Only that output is mounted;
no Avernet checkout, OCB checkout, source `node_modules` or `apps/frontend` is
available. From its `apps/frontend-nextgen`, install the locked dependencies,
run typecheck, selected tests and build. Registry access follows the private
deployment environment; credentials are supplied externally, never exported.

Then use the output root as the sole Docker build context:

```bash
docker build -f apps/frontend-nextgen/deploy/collab/Dockerfile .
```

The dedicated Dockerfile copies only paths within this output geometry and
uses nextgen's deployment assets. It must not fetch application code from either
source repository during build. Start the image, verify health and deep-link
fallback, then test private auth and retained collaboration/external-Bot flows
against configured upstreams. Confirm no task/admin requests and no navigation
to excluded or legacy pages. An internal registry/backend dependency is allowed;
a legacy frontend or source-checkout dependency is not.

- [ ] **Step 3: Record completion evidence per boundary**

Report separately: full Avernet CI, full public Singlebox smoke, full internal
assembly regression, export fixture tests, isolated install/typecheck/test/build,
image startup and target-stack browser/network smoke. Missing access to an
internal CI/backend means that boundary remains unverified; public CI success
does not complete the independent internal-site requirement. Mark the parent
spec complete only when all required boundaries have evidence.
