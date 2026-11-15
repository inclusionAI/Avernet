# Development rules

Read this before changing code. Module-local rules add to these repository-wide
rules within their scope. Architecture authority remains in `docs/arch/`;
contracts define interface meaning. Waivers must be explicit, owned, reviewed
and time-bounded. Fix an incorrect check rather than weakening it to pass.

## Development Guidelines

Start from the requirement and the existing contract. Keep changes small and
traceable.

### File Size

- Before completing a change, check the line count of every added or modified source file. A source file must not exceed 1,000 lines because CI enforces this limit.
- When a file exceeds or is approaching 1,000 lines, split it by responsibility and update its callers and tests before CI. Add a file to the CI allowlist only when it cannot reasonably be split; document the reason and follow-up cleanup plan in the change description.

- Do not add features that were not requested.
- Do not add speculative abstraction or configurability.
- Do not refactor unrelated code.
- Match local style in the files you touch.
- Remove only dead imports, variables, or helpers caused by your own change.
- Propagate database and persistence write failures as errors; never silently
  swallow failed writes and return success.
- Prefer structured parsers and APIs over ad hoc string manipulation.
- Keep public-local development free of company-only services, registries,
  domains, credentials, and runtime state.

### Python Type Contracts

- Use `T | None` only when `None` is an intentional, valid state in the domain
  contract or at an external input boundary.
- Keep required configuration values, request fields, constructor arguments,
  and service method parameters non-optional end to end.
- Do not widen a required type to `T | None` merely for defensive programming,
  uncertain call sites, test convenience, or a fallback that hides missing
  input. Validate at the boundary and fail clearly instead.
- Before introducing an optional type, verify that a real caller can omit the
  value and that the receiving code defines meaningful behavior for `None`.

## Testing Rules

Choose tests based on risk:

- Narrow implementation change: run the closest unit tests.
- Contract change: update and run the relevant conformance tests.
- Cross-module behavior change: run all affected module tests.
- Architecture boundary change: run the architecture tests and update docs.
- BCS Rust changes: read `apps/bcs/AGENTS.md` first, then run the relevant Cargo
  tests.

If you cannot run a required test, state exactly what was not run and why.

## File Hygiene

Do not commit:

- secrets, tokens, cookies, or private keys
- local databases or runtime state
- generated logs and cache directories
- machine-specific agent configuration
- private service endpoints or private package indexes

Open-source defaults must be reproducible from public dependencies or clearly
marked as TODO.

## Skills Architecture

Before changing Skills management, publication, mounting, or runtime
activation, read
`apps/backend/src/agentclaw/community/core/skill_center/AGENTS.md`.

- `skills-repo` and `skills-local` are complete content stores. An active
  Skills directory must expose only the Skills explicitly activated for the
  current Bot; do not add bridges from the active directory to a full content
  store.
- Do not add new engine-specific filesystem paths to Backend code. Physical
  layout ownership belongs to Engine Runtime and its versioned layout contract.
- Treat a `center://` source as a governed, versioned content source. A source
  prefix alone is not evidence that publication, distribution, and activation
  have completed.

## Frontend Design Tokens

The workbench frontend anchors its primary brand hue to a single canonical
color. New UI must reuse the shadcn semantic classes built on these tokens
instead of hardcoded palette colors or raw `text-white` / `bg-white` as a
primary surface.

- Canonical primary brand color: `#2563eb`, HSL `221.2 83.2% 53.3%`; consume it
  through the `--primary` / `--brand` semantic tokens in
  `apps/frontend-nextgen/src/global.css` rather than inlining hex or palette
  hues in component code.
- Prefer shadcn semantic classes (`bg-primary`, `text-primary-foreground`,
  `bg-secondary`, `bg-muted`, `text-muted-foreground`, `border-input`,
  `bg-background`, `bg-card`, `bg-destructive`, `bg-success/10`,
  `text-success`) over direct color literals.
