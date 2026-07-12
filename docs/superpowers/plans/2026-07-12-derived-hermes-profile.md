# Derived Hermes Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require only a Bot name in each Hermes onboarding method, derive a stable Hermes Profile automatically, and include both values in manual and bot-assisted onboarding.

**Architecture:** Keep profile derivation and command rendering in the existing pure `botAccess` helper. Replace the two-field form with one reusable Bot-name field, while each onboarding method owns independent name and touched state. The installer remains authoritative; the bot-assisted template and guide receive the same derived Profile and `--create-profile` semantics as the manual command.

**Tech Stack:** React 18, TypeScript, Jest, Umi Max, Python `unittest`, Bash 3.2+.

## Global Constraints

- Manual and bot-assisted Hermes onboarding each have their own Bot name value.
- No editable Profile input remains in either frontend surface.
- `Hermes Reviewer` derives to `avernet-hermes-reviewer`.
- A non-ASCII-only name derives to `avernet-bot-<8 lowercase hex digits>` using the specified FNV-1a algorithm.
- Derived profiles match `[a-z0-9][a-z0-9_-]{0,63}` and never use a reserved bare name.
- Empty Bot names never produce a command or instruction, and their copy button remains disabled.
- The human token stays on stdin for manual installation and never enters installer argv or logs.
- OpenClaw onboarding behavior and templates remain unchanged.
- Committed raw GitHub URLs remain on `refs/heads/dev`; a final-SHA local preview override remains uncommitted.

---

### Task 1: Derive Profiles And Render Both Hermes Methods

**Files:**
- Modify: `src/frontend/src/pages/BcnHome/lib/botAccess.ts`
- Test: `src/frontend/src/pages/BcnHome/lib/botAccess.test.ts`

**Interfaces:**
- Produces: `deriveHermesProfile(botName: string): string`.
- Changes: `HermesBotConfig` to `{ botName: string }`.
- Changes: `HermesBotConfigValidation` to `{ botNameError: string | null; valid: boolean }`.
- Preserves: `renderBotAccessCommand`, `replaceBotAccessToken`, and all OpenClaw behavior.

- [ ] **Step 1: Write failing derivation and rendering tests**

Replace profile-entry validation cases with these focused expectations:

```ts
expect(deriveHermesProfile('Hermes Reviewer')).toBe(
  'avernet-hermes-reviewer',
);
expect(deriveHermesProfile('  Hermes   Reviewer  ')).toBe(
  'avernet-hermes-reviewer',
);
expect(deriveHermesProfile('Hermes Réviewer')).toBe(
  'avernet-hermes-reviewer',
);
expect(deriveHermesProfile('产品经理')).toBe('avernet-bot-397dc3e8');
expect(deriveHermesProfile('a'.repeat(100))).toHaveLength(64);
expect(deriveHermesProfile('')).toBe('');

expect(validateHermesBotConfig({ botName: '' })).toEqual({
  botNameError: '请输入 Bot 名称',
  valid: false,
});
expect(validateHermesBotConfig({ botName: 'Hermes Reviewer' })).toEqual({
  botNameError: null,
  valid: true,
});

expect(
  renderBotAccessCommand(
    'manual {token} --bot-name {bot_name} --profile {profile} --create-profile',
    'registration-token',
    { botName: "Hermes O'Brien" },
  ),
).toBe(
  "manual registration-token --bot-name 'Hermes O'\\''Brien' " +
    "--profile 'avernet-hermes-o-brien' --create-profile",
);

expect(
  renderBotAccessCommand(
    'automatic {token} bot={bot_name} profile={profile}',
    'registration-token',
    { botName: '产品经理' },
  ),
).toBe(
  "automatic registration-token bot='产品经理' " +
    "profile='avernet-bot-397dc3e8'",
);
```

Update the notice expectation to:

```ts
expect(HERMES_MULTI_PROFILE_NOTICE).toBe(
  '支持接入多个 Hermes Bot。Avernet 会根据 Bot 名称自动创建独立 Profile；相同名称将恢复原 Bot。',
);
```

- [ ] **Step 2: Run the helper suite and verify RED**

Run:

```bash
cd src/frontend
npm test -- src/pages/BcnHome/lib/botAccess.test.ts --runInBand
```

Expected: FAIL because `deriveHermesProfile` is not exported and the current config still requires `profile`.

- [ ] **Step 3: Implement deterministic profile derivation**

Use these contracts and implementation in `botAccess.ts`:

```ts
export const HERMES_MULTI_PROFILE_NOTICE =
  '支持接入多个 Hermes Bot。Avernet 会根据 Bot 名称自动创建独立 Profile；相同名称将恢复原 Bot。';

export interface HermesBotConfig {
  botName: string;
}

export interface HermesBotConfigValidation {
  botNameError: string | null;
  valid: boolean;
}

const HERMES_PROFILE_PREFIX = 'avernet-';
const HERMES_PROFILE_MAX_LENGTH = 64;

function fnv1a32(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

export function deriveHermesProfile(botName: string): string {
  const trimmed = botName.trim();
  if (!trimmed) return '';

  const slug = trimmed
    .normalize('NFKD')
    .toLowerCase()
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');

  if (!slug) return `${HERMES_PROFILE_PREFIX}bot-${fnv1a32(trimmed)}`;

  const maxSlugLength = HERMES_PROFILE_MAX_LENGTH - HERMES_PROFILE_PREFIX.length;
  const shortened = slug.slice(0, maxSlugLength).replace(/-+$/g, '');
  return `${HERMES_PROFILE_PREFIX}${shortened}`;
}

export function validateHermesBotConfig(
  config: HermesBotConfig,
): HermesBotConfigValidation {
  const botNameError = config.botName.trim() ? null : '请输入 Bot 名称';
  return { botNameError, valid: !botNameError };
}
```

Inside `renderBotAccessCommand`, derive the profile instead of reading it from config:

```ts
if (hermes && !validateHermesBotConfig(hermes).valid) return '';

let command = template.replace('{token}', token);
if (hermes) {
  command = command
    .replace('{bot_name}', quoteShellArg(hermes.botName.trim()))
    .replace('{profile}', quoteShellArg(deriveHermesProfile(hermes.botName)));
}
return command;
```

Delete `HERMES_PROFILE_PATTERN`, `HERMES_RESERVED_PROFILES`, and all editable-profile validation branches.

- [ ] **Step 4: Run helper tests and frontend lint**

Run:

```bash
cd src/frontend
npm test -- src/pages/BcnHome/lib/botAccess.test.ts --runInBand
npm run ci
```

Expected: helper suite and lint exit zero.

- [ ] **Step 5: Commit the pure helper change**

```bash
git add src/frontend/src/pages/BcnHome/lib/botAccess.ts \
  src/frontend/src/pages/BcnHome/lib/botAccess.test.ts
git commit -m "feat(frontend): derive Hermes profiles from bot names"
```

---

### Task 2: Collect Independent Bot Names In Both Frontend Surfaces

**Files:**
- Create: `src/frontend/src/pages/BcnHome/components/HermesBotNameField.tsx`
- Delete: `src/frontend/src/pages/BcnHome/components/HermesBotConfigFields.tsx`
- Modify: `src/frontend/src/pages/BcnHome/components/AccessSection.tsx`
- Modify: `src/frontend/src/pages/BcnHome/components/AccessSection.test.ts`
- Modify: `src/frontend/src/pages/GroupChat/components/AddBotGuideModal.tsx`
- Modify: `src/frontend/src/pages/GroupChat/components/AddBotGuideModal.test.ts`

**Interfaces:**
- Consumes: `deriveHermesProfile`, `validateHermesBotConfig`, and `renderBotAccessCommand` from Task 1.
- Produces: `HermesBotNameField` with one controlled Bot-name input.
- Preserves: independent manual and automatic values across method and engine switches.

- [ ] **Step 1: Write failing landing-page interaction tests**

Change the Hermes automatic fixture to consume Bot and Profile placeholders:

```ts
bcnHermesAutoConnectCmdTemplate:
  'hermes automatic {token} --bot-name {bot_name} --profile {profile}',
```

After selecting Hermes, assert that both method cards have one Bot-name input and no Profile input:

```ts
expect(getInput(tree, 'bcn-access-hermes-manual-bot-name')).toBeDefined();
expect(getInput(tree, 'bcn-access-hermes-automatic-bot-name')).toBeDefined();
expect(textOf(tree)).not.toContain('Profile 名称');
expect(getCopyButtons(tree).map((button) => button.props.disabled)).toEqual([
  true,
  true,
]);
```

Drive distinct values and assert distinct generated outputs:

```ts
getInput(tree, 'bcn-access-hermes-manual-bot-name').props.onChange({
  target: { value: 'Hermes Manual' },
});
tree = render();
getInput(tree, 'bcn-access-hermes-automatic-bot-name').props.onChange({
  target: { value: 'Hermes Automatic' },
});
tree = render();

expect(commandsIn(tree)).toEqual([
  "hermes manual registration-token --bot-name 'Hermes Manual' " +
    "--profile 'avernet-hermes-manual' --create-profile",
  "hermes automatic registration-token --bot-name 'Hermes Automatic' " +
    "--profile 'avernet-hermes-automatic'",
]);
```

- [ ] **Step 2: Write failing workbench-modal interaction tests**

After selecting Hermes, fill the manual name, switch to automatic, and prove it has independent empty state:

```ts
getInput(tree, 'add-bot-guide-hermes-manual-bot-name').props.onChange({
  target: { value: 'Hermes Manual' },
});
tree = render();
expect(commandIn(tree)).toContain("--profile 'avernet-hermes-manual'");

getButton(tree, 'Bot 自动接入').props.onClick();
tree = render();
expect(getInput(tree, 'add-bot-guide-hermes-automatic-bot-name').props.value).toBe('');
expect(getCopyButton(tree).props.disabled).toBe(true);

getInput(tree, 'add-bot-guide-hermes-automatic-bot-name').props.onChange({
  target: { value: 'Hermes Automatic' },
});
tree = render();
expect(commandIn(tree)).toContain("--profile 'avernet-hermes-automatic'");

getButton(tree, '用户自助接入').props.onClick();
tree = render();
expect(getInput(tree, 'add-bot-guide-hermes-manual-bot-name').props.value).toBe(
  'Hermes Manual',
);
```

Assert both surfaces show `请先填写 Bot 名称。` for an empty active method and never render `Profile 名称`.

- [ ] **Step 3: Run both component suites and verify RED**

Run:

```bash
cd src/frontend
npm test -- \
  src/pages/BcnHome/components/AccessSection.test.ts \
  src/pages/GroupChat/components/AddBotGuideModal.test.ts \
  --runInBand
```

Expected: FAIL because the automatic method has no field, the profile field still exists, and both methods do not yet own independent state.

- [ ] **Step 4: Replace the two-field component with one Bot-name field**

Create `HermesBotNameField.tsx`:

```tsx
import React from 'react';

interface HermesBotNameFieldProps {
  idPrefix: string;
  botName: string;
  botNameError: string | null;
  onBotNameChange: (value: string) => void;
}

export const HermesBotNameField: React.FC<HermesBotNameFieldProps> = ({
  idPrefix,
  botName,
  botNameError,
  onBotNameChange,
}) => (
  <label
    className="block text-xs font-medium text-[#52606d]"
    htmlFor={`${idPrefix}-bot-name`}
  >
    Bot 名称
    <input
      id={`${idPrefix}-bot-name`}
      value={botName}
      onChange={(event) => onBotNameChange(event.target.value)}
      aria-invalid={botNameError ? true : undefined}
      aria-describedby={
        botNameError ? `${idPrefix}-bot-name-error` : undefined
      }
      placeholder="例如 Hermes Reviewer"
      className="mt-1 h-9 w-full rounded-md border border-[#d9e0ea] bg-white px-3 text-sm text-[#1a2332] outline-none focus:border-[#1d4ed8]"
    />
    {botNameError && (
      <span
        id={`${idPrefix}-bot-name-error`}
        className="mt-1 block text-xs text-[#dc2626]"
      >
        {botNameError}
      </span>
    )}
  </label>
);
```

Delete `HermesBotConfigFields.tsx` and update component-test resolver imports to `HermesBotNameField`.

- [ ] **Step 5: Add per-method state to `AccessSection`**

Use records keyed by `BotAccessMethodId`:

```ts
const [hermesBotNames, setHermesBotNames] = useState<
  Record<BotAccessMethodId, string>
>({ manual: '', automatic: '' });
const [hermesBotNameTouched, setHermesBotNameTouched] = useState<
  Record<BotAccessMethodId, boolean>
>({ manual: false, automatic: false });
```

Inside `ways.map`, compute each method independently:

```ts
const hermesMethod = selectedEngine.id === 'hermes';
const botName = hermesBotNames[item.id];
const validation = validateHermesBotConfig({ botName });
const botNameError = hermesBotNameTouched[item.id]
  ? validation.botNameError
  : null;
const command = renderBotAccessCommand(
  item.template,
  token ?? TOKEN_PLACEHOLDER,
  hermesMethod ? { botName } : undefined,
);
```

Render `HermesBotNameField` in both Hermes cards with `idPrefix` set to
`bcn-access-hermes-${item.id}`. Update the method-specific record in its
`onBotNameChange`. Disable only that card's copy button when its validation is
invalid. Replace neutral guidance with `请先填写 Bot 名称。`.

- [ ] **Step 6: Add per-method state to `AddBotGuideModal`**

Use the same two records. Derive the active value from `selectedMethod.id` and
render `HermesBotNameField` whenever the selected engine is Hermes:

```ts
const selectedMethodId = selectedMethod?.id ?? 'manual';
const hermesMethod = selectedEngine?.id === 'hermes';
const hermesBotName = hermesBotNames[selectedMethodId];
const hermesValidation = useMemo(
  () => validateHermesBotConfig({ botName: hermesBotName }),
  [hermesBotName],
);
const hermesBotNameError = hermesBotNameTouched[selectedMethodId]
  ? hermesValidation.botNameError
  : null;
```

Pass `{ botName: hermesBotName }` to `renderBotAccessCommand` for both Hermes
methods. Use `add-bot-guide-hermes-${selectedMethodId}` as the field prefix,
preserve both records across engine switches, and use `请先填写 Bot 名称。` when
the current method is empty.

- [ ] **Step 7: Run component and helper tests**

Run:

```bash
cd src/frontend
npm test -- \
  src/pages/BcnHome/lib/botAccess.test.ts \
  src/pages/BcnHome/components/AccessSection.test.ts \
  src/pages/GroupChat/components/AddBotGuideModal.test.ts \
  --runInBand
npm run ci
```

Expected: all focused suites and lint exit zero.

- [ ] **Step 8: Commit the frontend interaction change**

```bash
git add src/frontend/src/pages/BcnHome/components/HermesBotNameField.tsx \
  src/frontend/src/pages/BcnHome/components/HermesBotConfigFields.tsx \
  src/frontend/src/pages/BcnHome/components/AccessSection.tsx \
  src/frontend/src/pages/BcnHome/components/AccessSection.test.ts \
  src/frontend/src/pages/GroupChat/components/AddBotGuideModal.tsx \
  src/frontend/src/pages/GroupChat/components/AddBotGuideModal.test.ts
git commit -m "feat(frontend): configure both Hermes onboarding methods"
```

---

### Task 3: Carry Bot And Profile Through Bot-Assisted Onboarding

**Files:**
- Modify: `src/frontend/src/shell/extension.ts`
- Modify: `src/frontend/src/pages/BcnHome/lib/botAccess.test.ts`
- Modify: `src/bcs/docs/install-instructions/install-hermes.md`
- Test: `src/bcs/connectors/hermes/tests/test_cli.py`

**Interfaces:**
- Consumes: `{bot_name}` and `{profile}` rendering from Task 1.
- Produces: a bot-assisted Hermes instruction containing both generated values.
- Preserves: `refs/heads/dev`, human-token handling, China mirror options, and OpenClaw templates.

- [ ] **Step 1: Add failing production-template and guide tests**

In `botAccess.test.ts`, render the actual automatic template:

```ts
const template = getExt(AppExt).resources.bcnHermesAutoConnectCmdTemplate;
expect(template).not.toBeNull();
expect(
  renderBotAccessCommand(template ?? '', 'registration-token', {
    botName: 'Hermes Reviewer',
  }),
).toContain(
  "Bot name 'Hermes Reviewer' and Hermes Profile " +
    "'avernet-hermes-reviewer'",
);
```

In the existing Python test
`test_install_markdown_defines_executable_base_url_default_and_override`, add:

```python
self.assertIn('--bot-name "${BOT_NAME}"', markdown)
self.assertIn('--profile "${HERMES_PROFILE}"', markdown)
self.assertIn("--create-profile", markdown)
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd src/frontend
npm test -- src/pages/BcnHome/lib/botAccess.test.ts --runInBand
cd ../bcs/connectors/hermes
~/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_cli.CliTests.test_install_markdown_defines_executable_base_url_default_and_override
```

Expected: frontend fails because the automatic template lacks both placeholders; Python fails because the guide lacks `--create-profile`.

- [ ] **Step 3: Update the automatic resource template**

Set `bcnHermesAutoConnectCmdTemplate` in `extension.ts` to:

```ts
'Follow the instructions in https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/docs/install-instructions/install-hermes.md to join BCN (Bot Coordination Network) with Bot name {bot_name} and Hermes Profile {profile}; your token is {token}'
```

Do not modify either OpenClaw template or the manual Hermes `refs/heads/dev`
URLs.

- [ ] **Step 4: Update the bot-assisted installation guide**

Change the Preconditions section so a missing named profile is allowed when
the default profile is configured. Add `--create-profile` to the documented
installer command:

```bash
printf '%s\n' "${HUMAN_TOKEN}" | bash "$installer" \
  --human-token-stdin \
  --bot-name "${BOT_NAME}" \
  --profile "${HERMES_PROFILE}" \
  --create-profile \
  --bcs-endpoint "${BCS_HTTP_ENDPOINT}" \
  --bcs-ws-url "${BCS_WS_URL}"
```

Immediately below the block, explain that `--create-profile` clones a missing
named profile from `default`, never overwrites an existing profile, and the
installer rejects a stored Bot-name mismatch before registration.

- [ ] **Step 5: Run guide, frontend, and full Hermes tests**

Run:

```bash
/bin/bash -n src/bcs/docs/install-instructions/install-hermes.sh
cd src/bcs/connectors/hermes
~/.hermes/hermes-agent/venv/bin/python -m unittest tests.test_cli
cd ../../../frontend
npm test -- \
  src/pages/BcnHome/lib/botAccess.test.ts \
  src/pages/BcnHome/components/AccessSection.test.ts \
  src/pages/GroupChat/components/AddBotGuideModal.test.ts \
  --runInBand
npm run ci
```

Expected: Bash syntax, full Hermes CLI suite, all focused frontend suites, and
lint exit zero.

- [ ] **Step 6: Commit the bot-assisted flow**

```bash
git add src/frontend/src/shell/extension.ts \
  src/frontend/src/pages/BcnHome/lib/botAccess.test.ts \
  src/bcs/docs/install-instructions/install-hermes.md \
  src/bcs/connectors/hermes/tests/test_cli.py
git commit -m "feat(hermes): pass derived profiles to automatic onboarding"
```

---

### Task 4: Review, Verify, Publish, And Restore Local Preview

**Files:**
- Verify: all files changed in Tasks 1-3.
- Keep untracked: `.codegraph/`.
- Keep uncommitted after push: final-SHA Hermes URL override in `src/frontend/src/shell/extension.ts`.

**Interfaces:**
- Consumes: the complete derived-profile flow.
- Produces: updated PR #102 and a working local `8010` preview.

- [ ] **Step 1: Run complete local verification**

Run from the repository root:

```bash
set -e
/bin/bash -n src/bcs/docs/install-instructions/install-hermes.sh
(
  cd src/bcs/connectors/hermes
  ~/.hermes/hermes-agent/venv/bin/python -m unittest tests.test_cli
)
(
  cd src/frontend
  npm test -- \
    src/pages/BcnHome/lib/botAccess.test.ts \
    src/pages/BcnHome/components/AccessSection.test.ts \
    src/pages/GroupChat/components/AddBotGuideModal.test.ts \
    --runInBand
  npm run ci
)
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 2: Request final code review**

Review the range from `c0259f77a787b457ffb5679a4f5c15b00042b70a` through
the new implementation head against
`docs/superpowers/specs/2026-07-12-multi-hermes-onboarding-design.md`.
Critical and Important findings must be fixed and re-reviewed before push.

- [ ] **Step 3: Verify both frontend surfaces in a real browser**

At `http://127.0.0.1:8010/` and the workbench `接入 Bot` modal:

1. Select Hermes and confirm no Profile input exists.
2. Confirm manual and bot-assisted methods each have a Bot name field.
3. Confirm both copy buttons are disabled while their own field is empty.
4. Enter different names and verify each output contains its own quoted Bot
   name and derived Profile.
5. Switch methods and engines and verify both names persist independently.
6. Enter `产品经理` and verify `avernet-bot-397dc3e8` is generated.
7. Confirm OpenClaw output is unchanged.
8. Check desktop and 390px mobile screenshots for clipping or overlap.

- [ ] **Step 4: Push through the repository gate and verify PR head**

```bash
git -c http.proxy=http://127.0.0.1:7897 \
  -c https.proxy=http://127.0.0.1:7897 \
  push --porcelain fork HEAD:refs/heads/design/hermes-bcn-entry
HTTPS_PROXY=http://127.0.0.1:7897 \
  gh pr view 102 --repo inclusionAI/Avernet \
  --json url,state,headRefOid,headRefName,baseRefName,mergeable,reviewDecision
```

Expected: the pre-push BCS and frontend gates pass, PR #102 remains open, and
its head OID equals local `HEAD`.

- [ ] **Step 5: Restore the final-SHA local preview override**

After push, replace only the three committed Hermes `refs/heads/dev` URL
occurrences in `extension.ts` with the final full commit SHA. Keep OpenClaw on
`refs/heads/dev`, do not commit the override, and verify all three raw URLs
return HTTP 200 through the local proxy.

Run the two component suites and lint after the override. The pure helper suite
intentionally asserts committed `refs/heads/dev` and is expected to reject the
uncommitted preview override.

- [ ] **Step 6: Final state check**

```bash
git status --short --branch
curl -fsS -o /dev/null -w 'frontend=%{http_code}\n' http://127.0.0.1:8010/
curl -fsS -o /dev/null -w 'bcs_health=%{http_code}\n' http://127.0.0.1:21000/health
```

Expected: only `src/frontend/src/shell/extension.ts` and `.codegraph/` remain
uncommitted, and both local endpoints return 200.
