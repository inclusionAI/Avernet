# Published ProductProfile storage and Swarm DAG configuration

## New runtime/configuration storage

New creation uses [ProductProfileService v2](product_profile_service/README.md)
and its [five-table DDL](../sql/2026_11_26_product_runtime_v2.sql). The publication,
string IDs and process-local Methods below belong to **legacy v1** only.
V2 configuration durability and execution recovery have separate explicit
contracts; v1 SQL Task rows are not a recoverable v2 runtime snapshot.

Owner: L6 product composition. Production assembly uses `ProductProfileReader`
backed by `SqlProductProfileRepository`; `ProductCatalog` remains an immutable
snapshot for pure authoring, explicit test assemblies and capability projection.
There is no missing-row, latest-version or DB-error fallback to code definitions.

## Storage and publication contracts

Apply [the additive DDL](../sql/2026_11_23_context_collab_product_profile.sql)
before deploying this reader. It adds **one** table,
`ac_context_collab_product_profile`: the whole resolved definition, including
Swarm policies and DAGs, is stored as canonical JSON with SHA-256. Profile ID
comparison is case sensitive. Tenant/env are resolved from trusted current
context on each call, not captured by a singleton's first request. Existing
ProductInstance JSON continues to pin its exact `profile_ref` unchanged.

- [`create_product_profile`](operations/product_profile.py) only authors and
  validates. It does not publish, instantiate, dispatch or mutate a catalog.
- [`ProductProfilePublicationServiceProtocol`](profile_publication.py) is an
  internal Service API. `publish(ProductProfilePublishCommand(definition), actor)`
  inserts an immutable version; identical replay returns its **first** audit
  record, while different content under the same ref is a conflict.
- [`ProductProfilePublicationAuthorizer`](../g1_secure_govern/profile_publication.py)
  is independent of Collet creation authorization. The public DI default is
  **deny-all**. A deployment must explicitly install its publisher authority;
  no HTTP endpoint or user-controlled publisher override is provided.
- [`profile_codec.py`](profile_codec.py) owns storage schema v1 (not the product,
  Method or workflow version). It rejects unknown/duplicate JSON keys,
  discriminators, unavailable Method versions, redefined Method capabilities,
  wrong scalar types, nonfinite values and definitions over 1 MiB. Dataclass
  type tags come from a closed allowlist; no Python import/evaluation is allowed.
  MethodProfile values persist only their exact installed method/version ref.
- [`DesignatedOpenAdmission`](../l5_methods/publication.py) is now a serializable
  L5 policy value. The commission module identity-reexports it; its policy
  semantics and existing class checks are unchanged.

## Explicit import and cutover

1. Stop writers during the application's storage cutover and apply the DDL.
2. Establish the **target trusted tenant/env** in the deployment/import context;
   configure the dedicated publication authorizer for the authenticated operator.
3. Call `publication.import_profiles(approved_definitions, actor)` explicitly.
   The composition helper
   [`import_installed_product_profiles`](../../../../di/modules/task/context_collab_products.py)
   supplies this distribution's public code definitions, not private settings.
   Import is transactional and replayable. Any same-version conflict or missing
   historical `product_profile_ref` aborts the whole batch. Supply all required
   historical definitions, not only the newest version.
4. Verify the exact old refs via `ProductProfileReader.get(id, version)`, including
   custom profiles, then switch traffic to the new DI. Never delete old versions
   still referenced by instances. The normal request path does not create the
   table, import seeds, update a definition or choose latest.
5. Verify the target MySQL/OceanBase DDL, collation and transaction behavior
   separately. Local tests exercise actual SQLite, not a production deployment.

No credentials, environment-specific definitions or database state belong in
this repository. Tests explicitly seed isolated databases through publication;
this does not authorize production auto-seeding.

## Configuring a Swarm DAG

The configuration lives at
`definition.composition.required_methods[*].policy = SwarmWorkflowPolicy(workflow)`
on the required Swarm MethodTemplate. `definition.workflow` still selects the
**root Task** state/action contract; it is not repurposed for Stage scheduling.
`SwarmMethodPolicy(plan_service_ref)` retains dynamic capability selection.
Optional Method declarations are validated but not silently enabled.

[`swarm_workflow.py`](../l5_methods/swarm_workflow.py) declares immutable values:
workflow ref/version, nodes with an objective, candidate AgentRefs, exact I/O and
completion contract refs, `depends_on`, named `outputs` and explicit `inputs`.
Each input binds a direct predecessor's `from_node + from_output`. A dependency
alone is a control edge and transfers no artifact. Every output is required and
bounded. Limits are 256 nodes, 4096 edges, 256 candidates/node, 16 outputs/node,
64 inputs/node and 1–64 artifacts per output. Cycles and ambiguous/unknown port
sources fail before publication.

[`WorkflowPlanningService`](../workflow_planning.py) implements the existing
unified plan call. The first decision supplies the whole compiled graph and
one executor; subsequent decisions select the next READY node in configuration
order. Choice is uniform over typed configured candidates intersected with
actual valid participation. This is **serial** execution, including fork/AND
join graphs, not a concurrent worker scheduler. Stable graph/task IDs do not
provide restart recovery. The configured Agent is not automatically admitted,
selected for root Work or granted resource access.

## Artifact evidence and handoff

See [the L5 runtime contract](../l5_methods/README.md#managed-swarm-workflow).
Managed Stage values retain their compiled node/objective/digest and named
input/output bindings. Allocation atomically commits the selected executor and
frozen named input manifest against the full MethodStore plan/stage snapshot.
Completion atomically commits its accepted output manifest with SUCCEEDED and
allocation release. These compound operations are local MethodStore operations,
**not** a distributed transaction with SQL or external resources.

A host must explicitly bind `SwarmArtifactEvidenceValidator` to a read-only
immutable-resource/content/access adapter. Its default implementation fails
closed; old dynamic workers' `accepts=True` or nonempty paths are insufficient.
The adapter must prove exact revision immutability and content-contract validity,
and check current per-resource read access for the **selected Agent** at
allocation/dispatch. `grant_ref` alone never proves permission. No automatic
regrant, alternate Agent, mutable latest lookup or body-copy bypass is provided.
The local acceptance adapter uses real temporary content-addressed files and
revocation checks; it is test support, not a universal production resource store.

Managed reports must name every declared output, obey type/cardinality and
provide fixed Resource revisions. Flat `artifact_refs` are a strict ordered
manifest projection. Before child Work submission, the report service checks
actor/lease, output structure and actual content. Direct `complete_stage` also
checks pinned configuration, the allocated Agent's done child Task and its full
submitted/accepted manifest/actor evidence, then validates actual content again.
Generic Work payloads saying `verified` are not evidence. Same-ID evidence with
a different port/revision cannot replace a partially committed report.

The child events retain a versioned `swarm_artifact_manifest` envelope and the
legacy `artifacts` projection; root `standard_task@1` is not relaxed. Only
accepted successful named outputs feed downstream inputs. Collet.artifacts is
not automatically populated. Root submission/acceptance remain separate.

## Failure diagnostics and limits

- Unknown published version: inspect the trusted scope and explicit import;
  do not synthesize a definition or use latest.
- Immutable-version conflict: compare digests; publish a new product version.
- Validation capability unavailable: install a real read-only provider; never
  substitute an always-true content check.
- Input access revoked/version missing: repair the external capability or policy
  explicitly; the driver does not silently choose another Agent or revision.
- SQL child Task exists but Method history is lost: refuse redispatch. Profile
  reader reconstruction is supported, MethodStore restart continuation is not.
- SQL report committed but Method completion failed: retry the same full
  manifest while the allocation is live; changed evidence and DB/write errors
  are not swallowed. There is no exactly-once or automatic retry promise.

Acceptance evidence lives in the
[change delivery record](../../../../../../../specs/2026-11-23-context-collab-swarm-workflow-mode/delivery.md).
