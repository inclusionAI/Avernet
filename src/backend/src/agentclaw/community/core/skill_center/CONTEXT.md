# Skill Center

The Skill Center context governs Skill assets, their references, and the capabilities a Bot is intended to use.

## Language

**Effective Capability**:
A Skill or MCP that a Bot is intended to use. Its desired state is independent of whether Runtime delivery has converged.
_Avoid_: Active file, mounted asset

**Installation**:
The fact that one capability is currently effective for one Bot. Absence is the inactive state.
_Avoid_: Runtime link, membership

**Membership**:
A durable organizational reference from a SkillSet to a capability. Membership alone does not imply that the capability is effective.
_Avoid_: Installation, activation

**Deactivation**:
The explicit removal of an Effective Capability from a Bot while retaining the Skill asset and its other references.
_Avoid_: Delete, retire, offline

**Skill Offline**:
A recoverable lifecycle state that prevents new consumption while preserving the Skill asset, versions, and historical references.
_Avoid_: Delete, deactivate

**Asset Deletion**:
The irreversible destruction of an unreferenced Skill asset. It must not implicitly change any Bot's Effective Capabilities or remove saved references.
_Avoid_: Deactivation, offline, retirement

**Blocking Reference**:
Any Installation, Membership, published Service Artifact lineage, Draft, Publication, or Version relationship that prevents Asset Deletion.
_Avoid_: Warning, impact hint

**Missing Source**:
A Skill whose governed source content is unavailable while its asset or references remain. Missing Source is a recoverable delivery problem, not permission to deactivate or delete the Skill.
_Avoid_: Deleted Skill, inactive Skill

**Recorded Local Skill Locator**:
The stable `local://` identity persisted for one Bot-local Skill. Replacing its package preserves this value even when Runtime resolves the package through a compatible location.
_Avoid_: Runtime path, current physical path

**Runtime Package Location**:
The location where the selected Engine and Layout currently store one Bot-local Skill package. It may differ in representation from the Recorded Local Skill Locator while identifying the same package.
_Avoid_: Recorded locator, database path

**Legacy Locator Alias**:
A narrowly recognized historical Recorded Local Skill Locator that resolves to the same Runtime Package Location under an explicit compatibility rule. It is a read compatibility form, not a current write format or a data migration.
_Avoid_: Canonical locator, path rewrite, migration
