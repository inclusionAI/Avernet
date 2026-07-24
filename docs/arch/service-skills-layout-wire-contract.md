# Service Skills Layout Wire Contract

This contract governs the Backend → BaaS deploy config → container image handoff
for versioned service-bot Skills manifests. It is deliberately narrower than a
general engine-layout descriptor.

## Contract v1

The Backend derives these immutable environment variables from the target
publish record's `skills_manifest` on first release, update, restart, rollback,
and recreate:

| Variable | Allowed value | Meaning |
|---|---|---|
| `AGENTCLAW_SKILLS_LAYOUT` | `legacy` or `pool` | Repo mount layout recorded in the Skills manifest |
| `AGENTCLAW_SKILLS_LAYOUT_CONTRACT_VERSION` | empty for Legacy; `skills-pool-p3-v1` for Pool | Exact Pool directory contract supported by the image |

Rules:

1. The Backend validates the manifest engine against the Bot engine before
   emitting either value.
2. Pool manifests must declare exactly `skills-pool-p3-v1`; missing or unknown
   versions fail closed before BaaS publication.
3. A compatible image selects the canonical Pool repo only when layout is
   `pool` and the contract version is exactly `skills-pool-p3-v1`.
4. A Pool declaration with a missing or unknown contract falls back to the
   Legacy mount and cannot advertise Pool readiness.
5. `legacy` always selects the Legacy repo. When both variables are absent, the
   image retains the pre-contract filesystem heuristic for old Backend and
   non-service compatibility.
6. Old images may ignore these added environment variables. Consequently a new
   Backend remains compatible with old images for Legacy manifests; Pool
   publication is enabled only after compatible images are rolled out.
