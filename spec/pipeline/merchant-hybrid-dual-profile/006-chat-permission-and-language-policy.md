# Claude chat permission and interaction policy

## Requirement

The `merchant_hybrid` platform-data Claude Code bot is a chat-first analyst.
It must run with `permission_mode=bypassPermissions` so a normal BCS chat is
not suspended on Claude Code's `ExitPlanMode` approval. The static role prompt
must forbid `AskUserQuestion` (including the lower-case spelling) and require
the bot to return missing information, ambiguity, and requested clarification
as ordinary final response text.

The bypass permission setting does not authorize business actions. The role's
existing read-only, no-external-action boundary remains in effect.

## Acceptance

1. The Claude profile emits `bypassPermissions` as the relay and BaaS runtime
   permission mode.
2. The expanded static system prompt contains the no-`AskUserQuestion` rule.
3. A directed, read-only BCS chat reaches a terminal final without an
   `ExitPlanMode` interaction.
4. Existing profile and live merchant-hybrid acceptance tests pass without
   logging message bodies, session content, or credentials.
