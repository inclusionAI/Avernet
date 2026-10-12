# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this module is

`apps/frontend-nextgen` is the next-generation Open Core web frontend for Avernet (a multi-bot AI workbench). It is exported from the internal TeamClaw source (`OPEN_CORE_MANIFEST.json` records the source commit) and coexists with the legacy `apps/frontend`. Singlebox serves it with `FRONTEND_VARIANT=nextgen` (see root `docs/singlebox-nextgen-local.md`).

Stack: UmiJS Max (`@umijs/max` 4.7.6, React 18) · Zustand · Tailwind CSS 4 · Radix UI + lucide-react · chat UI from the `@tc-chat/*` SDK. Node 22 (CI and Dockerfile pin it; `npm ci` is the install command, `postinstall` runs `max setup`).

The repo-wide root `AGENTS.md` also applies (architecture constitution, file-size limit, PR conventions, pre-push hooks).

## Commands

```bash
npm ci                                                # install (generates src/.umi via postinstall)

# Dev server (proxy preset defaults to `local`; TARGETS overridable via env)
TEAMCLAW_GW_BASE=http://127.0.0.1:8888 npm run dev:local   # PRESET=local MOCK=none
npm run dev                                           # same, without explicit PRESET/MOCK

npm run typecheck        # tsc --noEmit
npm run lint             # max lint
npm test                 # jest (tests live in test/, not colocated)
npx jest test/foo.test.ts                    # single file
npx jest test/foo.test.ts -t "case name"     # single test case

npm run build            # max build → dist/
npm run ci               # typecheck + lint + test --runInBand + build  (the CI gate)
npm run check:open-core  # open-core boundary check — run before anything else looks at a PR
```

CI for this module is `.github/workflows/frontend-nextgen.yml`: `npm ci` → `check:open-core` → `npm run ci`. Run `npm run check:open-core && npm run ci` before pushing.

Test environment: **node by default**; tests needing DOM must start with a `/** @jest-environment jsdom */` docblock. Jest maps `@tc-chat/ui/es/*` → its CJS `lib/` and transforms ESM `@tc-chat/*`; see the comments in `jest.config.cjs` if imports fail in tests.

## Open Core boundary (critical)

This directory is a public open-source artifact. Internal-TeamClaw capabilities are deliberately stripped and must stay out:

- Never add internal registries (`registry.antgroup-inc.cn`), `npm:@alipay/*` aliases, `@alipay/bigfish`, company-only domains, private endpoints, or credentials. `scripts/check-open-core.cjs` fails the build on these.
- Forbidden paths: `.internal-paths`, `src/internal`, `src/extensions/internal.ts`, `config/internal`, `config/routes.internal.ts`.
- Code comments often reference the "internal overlay" — internal-only behavior is injected at defined seams (see below), never inlined behind flags.
- Changes made here must be carried back to the TeamClaw Open Core baseline before the next export.

## Architecture

### Umi Max conventions

- `config/config.ts` is the base; `config/config.local.ts` holds the dev proxy table and `define` constants, driven by `PRESET` (`local|dev|pre|prod` from `config/presets.config.ts`) and overridable via `TEAMCLAW_*` / `BCS_ENDPOINT_*` / `TASK_ENGINE_UPSTREAM` env vars. Real server addresses live only in env or the internal overlay — `config/servers.config.ts` keeps localhost placeholders.
- Proxy path order matters: narrow routes (`/openapi/v1/bots/{work-orders,work-order-notifications,spaces}` → admin upstream; `/openapi/v1/collaboration/tasks` → task engine) must precede the broad `/openapi` gateway catch-all; umi proxy matches by key insertion order.
- `config/routes.ts` is an explicit route table; deep links are preserved via redirects, never left to 404 (the `*` catch-all renders `pages/NotFound`).
- `src/app.tsx` is the runtime entry: `extendCapabilities(appExtension.capabilities)` then `sealExtensions()` at boot; `rootContainer` mounts global no-UI observers (`GatewayLoginRedirector`, `ErrorNotifyObserver`), global modals, and the Toaster.
- `@/` → `src/`, `@@/` → `src/.umi`.

### Internal-overlay seams

Two mechanisms keep Open Core vs internal variance out of feature code:

- **Capabilities** (`src/capabilities/`): a registry extended at boot then sealed. Everything environment-dependent (login strategy, nav items, metrics, help links, brand) is read via `getCapabilities()`, not branched on in components.
- **Extensions alias** (`src/extensions/`): `config.ts` maps `@/extensions` → `src/extensions/empty` (Open Core assembly). The internal build overrides the alias to `internal.ts`. `registerSidePanelWiring()` here wires the `@tc-chat/ui` side-panel SDK.

When adding behavior: Open Core defaults go in `extensions/empty.ts` + `defaultCapabilities.ts`; wrap rather than fork existing components.

### Layering

`pages/` → `shell/` + `components/` → `stores/` (Zustand, one store per feature) + `services/` → `domain/` (pure logic, no I/O).

- `src/shell/` — app chrome: `AppShell`, sidebar, `navigation.tsx` (nav grouped into collab/bot/legacy sections; internal entries injected via capabilities), `routeMeta.ts`.
- `src/services/` — API clients grouped by backend: `backendApi/` (TeamClaw gateway `/openapi/v1/**`, plus auth/task/admin boards), `bcs/` (bot coordination, `/auth/*`, UMD side panels), `engine/`, etc.
- `src/adapters/` — runtime adapter profiles (bot-runtime, bot-capability, bot-ui, request) with defaults that internal builds can replace.
- `src/assets/` — TaskPanel side-screen assets shipped to BCS as UMD. ESLint forbids importing `@/components`, `@/hooks`, `@/stores`, `@/pages`, `@/domain` here. Inject user context by wrapping in `extensions/` instead (pattern: `TaskPanelAdapterWithUser` in `extensions/empty.ts`).

### Request handling: two channels, shared error path

- **Channel A**: umi `request` (`src/requestConfig.ts`) → `RequestProtocolError`.
- **Channel B**: raw-fetch `httpClient` (`src/services/backendApi/httpClient.ts`) → `BackendRequestError`. Supports `target: 'legacy-agentclaw'`, retry, blob/text responses, userId injection.

Both channels produce isomorphic errors carrying `toastKey` + `alreadyHandled`. Protocol layers only `enqueue` into `errorNotifyStore` and throw — no direct toasts in services; the top-level `useErrorNotifyObserver` drains the queue. Auth failures resolve through `resolveAuthFailureDisposition` (login strategy comes from capabilities: `oauth-provider` Open Core vs `ace-gateway` internal) into a single-flight `AceLoginRedirectError` consumed by `useGatewayLoginRedirect`. Keep this pattern when adding API calls.

### Auth

OAuth goes through BCS `/auth/*` with an HttpOnly `bcs_session` cookie; JavaScript must never read or persist the JWT. Production runs behind same-origin Nginx routing (`deploy/nginx/`) with mandatory upstream env vars — see `docs/deployment.md`.

### antd exception

`antd@6.6.1` is pinned solely to satisfy `@tc-chat/ui`'s peer dependency. Application code must not import `antd`; use Radix UI components (`src/components/ui`), Tailwind, and `sonner` for toasts instead. Re-audit this exception whenever `@tc-chat/ui` is upgraded.

## Conventions

- Comments are largely Chinese; match the local style of the file you touch.
- Source files must not exceed 1,000 lines (repo CI enforces; split by responsibility).
- PR titles: `<type>(frontend-nextgen): <concise outcome>` (e.g. `fix(frontend-nextgen): remove external bot entry from identity selector`). Root `AGENTS.md` defines the full PR description format.
- Do not add unrequested features or speculative abstraction; keep changes small and traceable.
