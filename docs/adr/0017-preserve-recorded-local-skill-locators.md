---
status: accepted
---

# Preserve recorded Local Skill locators through runtime alias resolution

A Bot-local Skill's recorded `local://` locator remains stable across package replacement, while Skill Center resolves explicitly supported historical aliases to the package's current Runtime Package Location. This avoids rewriting historical database identity when an AICoding runtime supersedes a Claude Code filesystem namespace, and it lets upload, query, and deletion share one fail-closed compatibility boundary instead of interpreting paths independently.

## Consequences

New Skill creation records the Backend-resolved location for the Bot's current Engine and Layout. Replacement preserves the existing locator byte for byte; an Engine-returned `target_path` is execution evidence rather than database authority. The first compatibility rule is limited to AICoding running the Legacy layout and to exact same-Bot, same-Skill Claude Code aliases. It does not cross into Pool layout, accept arbitrary prefixes, repair missing packages, or change any other Engine's locator policy.

Because historical rows are not migrated, alias reading is durable compatibility rather than a temporary fallback that can be removed when the AIX package-apply capability ships. Removing it requires a separately governed migration with proof that no supported historical locator remains.

## Considered options

Bulk rewriting historical locators was rejected because the stored value is an observable identity consumed by existing flows and because a database rewrite cannot by itself prove Runtime content convergence. Requiring raw locator equality was rejected because Host/NAS and Engine address views can denote the same package. Permissive prefix replacement was rejected because it could authorize a different Bot, Skill, Engine, or Layout.
