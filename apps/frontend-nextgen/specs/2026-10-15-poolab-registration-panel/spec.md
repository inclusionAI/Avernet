# Poolab registration panel inside the external Bot onboarding dialog

Status: implemented. Implementation completed on 2026-10-15 pending local
verification (jest binary unavailable in this checkout; commands/services are
covered by deterministic unit tests over exported builders).

Owned externally and not modified: backend, BCS, 一键接入脚本（Poolab 团队维护
OSS dist）与语雀接入指南。本实现仅在 frontend-nextgen 完成「Poolab Tab +
双模式接入引导 + 环境感知」的 UI/能力注入，参考老前端 PR #2966。

Owner: frontend next-gen. Scope: dialog UI + capability contract default +
internal overlay wiring. Backend, BCS, the OSS onboarding script, and any
Poolab-specific runtime stay authoritative in their existing owners.

## Background

The BotWorkshop dialog at *Bot 管理 → 外部 Bot → 接入外部 Bot* currently offers
two engines through a `Segmented`: `openclaw` and `qwenwork`. The internal
deployment must add a third option `poolab`, mirroring the legacy open-claw
guidance merged in PR #2966, including its dual-mode commands and its
environment (`PRE`/`PROD`) awareness.

Poolab registration needs a BCS self-service registration token. The existing
`useBotRegistrationToken(open, providerSlug)` hook accepts any provider slug
and routes through `getRegistrationProvider` + `getBotRegistrationToken`; the
internal BCS deployment enables Poolab self-service in `pre` (`prv_2d0f45f5`)
and `prod` (`prv_d2dad573`), but not in `local` / `dev`. The dialog therefore
has to reason about the active runtime environment, which
`getRuntimeEnvironment()` already provides.

The Poolab journey does not reuse `QwenWorkRegistrationPanel`: Poolab keeps a
single command binary (`bcs-bridge`) plus an OSS-hosted onboarding shell
script, and uses simpler `register` / `start --daemon` / `status` invocations.
Panel content differs enough that a sibling component is preferable to
extending the QwenWork form.

## Accepted design

- The dialog keeps a single `Segmented` with three options in the order
  `openclaw` → `qwenwork` → `poolab`. Poolab content renders inside a new
  `PoolabRegistrationPanel` component placed alongside
  `QwenWorkRegistrationPanel`.
- Environment awareness uses `getCapabilities().getRuntimeEnvironment()`:
  - `PRE` and `PROD` are supported. The Poolab pre-registration hook is invoked
    only when the dialog is open and an environment-supported panel is visible.
  - `LOCAL` and `DEV` show a disable hint; no token request is issued and no
    copyable command is rendered.
- A second `Segmented` inside the Poolab panel switches between
  `quick` (one-click script, recommended) and `manual` (step-by-step).
  Both modes embed the freshly fetched token and the editable Bot name.
- The Bot name defaults to `<displayName>的Poolab` where `displayName` comes
  from `useHumanIdentity().identity?.displayName`. An `Input` allows the user
  to override within the same validation rules as QwenWork
  (`getBotRegistrationNameError`).
- Internal deployments inject the script URL and a published onboarding guide
  URL via a new capability `getPoolabBridgeInstallation()`. Open Core defaults
  to `null`, which keeps the Poolab tab hidden entirely (subsequent guards
  bail out before rendering any internal URLs). The internal overlay in
  `apps/frontend-nextgen/src/extensions/internal.ts` returns the real values:
  - `oneClickScriptUrl`: `http://antsys-dais-dataai-vcs.oss-cn-hangzhou-zmf.aliyuncs.com/bcn/dist/poolab-onboard.sh`
  - `guideUrl`: `https://yuque.antfin.com/bcn/bcn/erxx2t0x2wtz3cue`
- Command assembly happens at the panel level so tests can target the strings:
  - Quick mode wraps the script URL with the environment prefix only when the
    environment is `PRE` (`SERVER_ENV=pre ` is omitted in `PROD`).
  - Manual mode lists `acli install bcs-bridge`, the `bcs-bridge register
    --token … --bot-name …` invocation with the same optional prefix,
    `bcs-bridge start --daemon`, and `bcs-bridge status`.
  - Token and Bot name arguments are shell-quoted using the shared
    `quoteShellArg` helper, never concatenated raw.
- Environment switching at runtime is not a goal; the dialog is opened by the
  active window and caches the resolved environment once per open. Tests may
  mock the capability directly.

## Acceptance criteria

1. When `getPoolabBridgeInstallation()` returns a non-null value and the
   active dialog engine is `poolab`, the panel renders the installation
   guidance, an editable Bot name input, and a way to fetch/retry the token.
2. When the runtime environment is `PRE` or `PROD` and a token has been
   returned, the panel shows the assembled one-click command (quick mode) or
   the four manual steps (manual mode). Manual commands copy individually; the
   one-click command copies as a whole. The `SERVER_ENV=pre ` prefix appears
   in PRE only, never in PROD.
3. When the runtime environment is `LOCAL` or `DEV`, the panel shows the
   disable hint, no token request is issued, and neither quick nor manual
   commands render.
4. When `getPoolabBridgeInstallation()` returns `null` (Open Core default),
   the Poolab option is not listed in the dialog `Segmented`, and the panel is
   unreachable. The Open Core build artifact stays free of the internal
   script URL and internal guide URL.
5. Token errors surface an inline retry action; switching modes does not
   refetch the token. Bot name edits re-assemble commands without refetching.
6. The internal overlay override exposes the script URL and the guide URL via
   the new capability type; TypeScript types compile against the new
   signature for both Open Core and internal builds.

## Validation plan

- New hook and panel unit tests inside `submodules/avernet/apps/frontend-nextgen/test`:
  - environment prefix logic (PRE / PROD / unsupported),
  - capability null vs non-null behavior,
  - mode switching without re-token,
  - Bot name validity gate (reuse `getBotRegistrationNameError` cases),
  - copy buttons use shell-quoted arguments.
- Overlay test in `apps/frontend-nextgen/test/internal/extensions.test.ts`
  (or its existing extension overlay suite): asserts the internal overlay
  returns the real script URL and does not regress other capability values.
- Type check, lint, and Jest across the affected workspaces.
- Manual verification: open the dialog in an internal dev assembled tree,
  toggle environments via mocked capability, and confirm all copyable strings
  match the expected commands.
