# Multi-Hermes BCN Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users create and reconnect multiple Hermes BCN bots safely by giving every bot an explicit named Hermes profile.

**Architecture:** Extend the Hermes installer with one authoritative `--create-profile` lifecycle and profile/name conflict guard. Keep command validation and shell rendering in the existing pure frontend access helper, then reuse one small field component in both the landing page and workbench modal.

**Tech Stack:** Bash 3.2+, Python `unittest`, React 18, TypeScript, Jest, Tailwind CSS, Umi Max.

## Global Constraints

- Each BCN Hermes bot uses a separate named Hermes profile.
- Profile identifiers match `[a-z0-9][a-z0-9_-]{0,63}` and reject `default`, `hermes`, `test`, `tmp`, `root`, and `sudo` in this multi-instance flow.
- A missing profile is created with `hermes profile create <profile> --clone-from default` only when `--create-profile` is explicit.
- An existing profile with the same stored bot name resumes idempotently; a non-empty different stored name fails before registration.
- The human registration token stays on stdin and never enters installer argv or logs.
- OpenClaw onboarding behavior does not change.
- The committed frontend resource URLs remain on `refs/heads/dev`; the local `8010` preview may use a commit SHA only as an uncommitted override.

---

### Task 1: Add Authoritative Installer Profile Lifecycle

**Files:**
- Modify: `src/bcs/docs/install-instructions/install-hermes.sh`
- Test: `src/bcs/connectors/hermes/tests/test_cli.py`

**Interfaces:**
- Produces: installer flag `--create-profile`.
- Produces: shell functions `validate_named_profile`, `ensure_hermes_profile`, and `session_bot_name`.
- Consumes: existing `resolve_python`, `valid_session`, `build_resume_command`, and Hermes CLI.

- [ ] **Step 1: Write failing profile lifecycle tests**

Add tests that source the real installer and exercise the shell helpers with a
fake Hermes executable:

```python
def test_installer_create_profile_requires_named_profile(self) -> None:
    command = (
        f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
        "validate_named_profile '' 1"
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command], capture_output=True, text=True
    )
    self.assertNotEqual(0, result.returncode)
    self.assertIn("--create-profile requires --profile", result.stderr)

def test_installer_creates_missing_named_profile_from_default(self) -> None:
    bin_dir = Path(self.tempdir.name) / "profile-bin"
    home = Path(self.tempdir.name) / "home"
    profile_home = home / ".hermes" / "profiles" / "reviewer"
    bin_dir.mkdir()
    hermes = bin_dir / "hermes"
    hermes.write_text(
        "#!/bin/sh\n"
        "test \"$1 $2 $3 $4\" = 'profile create reviewer --clone-from' || exit 9\n"
        "test \"$5\" = 'default' || exit 10\n"
        "mkdir -p \"$HOME/.hermes/profiles/reviewer\"\n"
        "printf 'model: inherited\\n' > \"$HOME/.hermes/profiles/reviewer/config.yaml\"\n",
        encoding="utf-8",
    )
    hermes.chmod(0o700)
    command = (
        f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
        'ensure_hermes_profile reviewer "$HOME/.hermes/profiles/reviewer" 1'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertTrue((profile_home / "config.yaml").is_file())
```

Add focused tests for valid underscore/hyphen names, the six rejected names,
65-character names, and an existing configured profile that must not invoke
`hermes profile create`.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
~/.hermes/hermes-agent/venv/bin/python -m unittest \
  tests.test_cli.CliTests.test_installer_create_profile_requires_named_profile \
  tests.test_cli.CliTests.test_installer_creates_missing_named_profile_from_default
```

Working directory: `src/bcs/connectors/hermes`.

Expected: FAIL because `validate_named_profile` and
`ensure_hermes_profile` do not exist.

- [ ] **Step 3: Implement profile validation and creation**

Add Bash 3.2-compatible helpers before `main`:

```bash
validate_named_profile() {
  local profile="$1" create_profile="$2"
  if [[ "$create_profile" == "1" && -z "$profile" ]]; then
    fail "--create-profile requires --profile"
  fi
  [[ -z "$profile" ]] && return 0
  [[ ${#profile} -le 64 && "$profile" =~ ^[a-z0-9][a-z0-9_-]*$ ]] \
    || fail "profile must match [a-z0-9][a-z0-9_-]{0,63}"
  case "$profile" in
    default|hermes|test|tmp|root|sudo)
      fail "profile name is reserved: $profile"
      ;;
  esac
}

ensure_hermes_profile() {
  local profile="$1" hermes_home="$2" create_profile="$3"
  [[ -f "$hermes_home/config.yaml" ]] && return 0
  [[ "$create_profile" == "1" ]] \
    || fail "Hermes profile is not configured: $hermes_home"
  hermes profile create "$profile" --clone-from default
  [[ -f "$hermes_home/config.yaml" ]] \
    || fail "Hermes profile creation did not produce config.yaml: $profile"
}
```

Parse `--create-profile`, document it in `usage`, reject it without
`--profile`, and call `ensure_hermes_profile` before the existing configured
profile check.

- [ ] **Step 4: Add RED tests for profile/name conflicts and resume args**

Add:

```python
def test_installer_rejects_different_bot_name_for_registered_profile(self) -> None:
    session = Path(self.tempdir.name) / "session.json"
    session.write_text(
        json.dumps({
            "bot_uuid": "bot-existing",
            "bot_token": "secret",
            "bcs_url": "ws://127.0.0.1:21000/ws/bot",
            "bot_name": "hermes2",
        }),
        encoding="utf-8",
    )
    command = (
        f"source {subprocess.list2cmdline([str(INSTALLER)])}; "
        'reject_profile_bot_name_mismatch "$1" hermes4 reviewer'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command, "conflict", str(session)],
        capture_output=True,
        text=True,
    )
    self.assertNotEqual(0, result.returncode)
    self.assertIn("reviewer is already registered as hermes2", result.stderr)
```

Extend `test_installer_resume_command_preserves_selected_options` to assert
`--create-profile` appears in `RESUME_COMMAND`.

- [ ] **Step 5: Run the conflict tests and verify RED**

Expected: FAIL because `reject_profile_bot_name_mismatch` is missing and the
resume command does not preserve the new option.

- [ ] **Step 6: Implement the conflict guard and resume preservation**

Add:

```bash
session_bot_name() {
  ensure_python || return 1
  "$PYTHON_CMD" - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
name = value.get("bot_name")
print(name if isinstance(name, str) else "")
PY
}

reject_profile_bot_name_mismatch() {
  local session="$1" requested_name="$2" profile="$3" stored_name=""
  stored_name="$(session_bot_name "$session")" || return 1
  if [[ -n "$stored_name" && "$stored_name" != "$requested_name" ]]; then
    fail "profile $profile is already registered as $stored_name; choose another profile"
  fi
}
```

Call the guard after `existing_valid=1` is known and before registration or
replacement handling. Append `--create-profile` to `resume_args` when selected.

- [ ] **Step 7: Run installer tests and commit**

Run:

```bash
/bin/bash -n src/bcs/docs/install-instructions/install-hermes.sh
~/.hermes/hermes-agent/venv/bin/python -m unittest tests.test_cli
```

Expected: shell syntax passes and all Hermes CLI tests pass.

Commit:

```bash
git add src/bcs/docs/install-instructions/install-hermes.sh \
  src/bcs/connectors/hermes/tests/test_cli.py
git commit -m "feat(hermes): create isolated profiles during onboarding"
```

---

### Task 2: Add Pure Frontend Validation And Command Rendering

**Files:**
- Modify: `src/frontend/src/pages/BcnHome/lib/botAccess.ts`
- Modify: `src/frontend/src/pages/BcnHome/lib/botAccess.test.ts`
- Modify: `src/frontend/src/shell/extension.ts`
- Modify: `src/frontend/src/shell/types.ts`

**Interfaces:**
- Produces: `HermesBotConfig`, `validateHermesBotConfig`, `quoteShellArg`, and `renderBotAccessCommand`.
- Preserves: `replaceBotAccessToken(template, token)` for existing OpenClaw callers.
- Consumes: resource template placeholders `{token}`, `{bot_name}`, and `{profile}`.

- [ ] **Step 1: Write failing pure helper tests**

Add tests for validation and rendering:

```ts
expect(validateHermesBotConfig({ botName: '', profile: '' })).toEqual({
  botNameError: '请输入 Bot 名称',
  profileError: '请输入 Profile 名称',
  valid: false,
});

expect(
  validateHermesBotConfig({ botName: 'Hermes Reviewer', profile: 'review_bot-2' }),
).toEqual({ botNameError: null, profileError: null, valid: true });

for (const profile of ['default', 'hermes', 'test', 'tmp', 'root', 'sudo']) {
  expect(validateHermesBotConfig({ botName: 'Reviewer', profile }).valid).toBe(false);
}

expect(quoteShellArg("Hermes O'Brien")).toBe("'Hermes O'\\''Brien'");

expect(
  renderBotAccessCommand(
    'run {token} --bot-name {bot_name} --profile {profile} --create-profile',
    'registration-token',
    { botName: "Hermes O'Brien", profile: 'review_bot-2' },
  ),
).toBe(
  "run registration-token --bot-name 'Hermes O'\\''Brien' --profile 'review_bot-2' --create-profile",
);
```

- [ ] **Step 2: Run Jest and verify RED**

Run:

```bash
npm test -- src/pages/BcnHome/lib/botAccess.test.ts --runInBand
```

Expected: FAIL because the new helpers are not exported.

- [ ] **Step 3: Implement validation and safe rendering**

Add the exact public types and functions:

```ts
export interface HermesBotConfig {
  botName: string;
  profile: string;
}

export interface HermesBotConfigValidation {
  botNameError: string | null;
  profileError: string | null;
  valid: boolean;
}

const HERMES_PROFILE_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const HERMES_RESERVED_PROFILES = new Set([
  'default', 'hermes', 'test', 'tmp', 'root', 'sudo',
]);

export function validateHermesBotConfig(
  config: HermesBotConfig,
): HermesBotConfigValidation {
  const botName = config.botName.trim();
  const profile = config.profile.trim();
  const botNameError = botName ? null : '请输入 Bot 名称';
  let profileError: string | null = null;
  if (!profile) profileError = '请输入 Profile 名称';
  else if (!HERMES_PROFILE_PATTERN.test(profile)) {
    profileError = '仅支持小写字母、数字、连字符和下划线，最长 64 位';
  } else if (HERMES_RESERVED_PROFILES.has(profile)) {
    profileError = '该 Profile 名称不可用于多 Bot 接入';
  }
  return { botNameError, profileError, valid: !botNameError && !profileError };
}

export function quoteShellArg(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

export function renderBotAccessCommand(
  template: string,
  token: string,
  hermes?: HermesBotConfig,
): string {
  let command = template.replace('{token}', token);
  if (hermes) {
    command = command
      .replace('{bot_name}', quoteShellArg(hermes.botName.trim()))
      .replace('{profile}', quoteShellArg(hermes.profile.trim()));
  }
  return command;
}
```

Implement `replaceBotAccessToken` as a compatibility wrapper around
`renderBotAccessCommand(template, token)`.

- [ ] **Step 4: Update the Hermes resource template**

Keep committed URLs on `refs/heads/dev` and append:

```text
--bot-name {bot_name} --profile {profile} --create-profile
```

Update resource comments in `shell/types.ts` to document all placeholders.
Extend the security test to assert the rendered installer argv contains the
three flags while the registration token remains absent from argv.

- [ ] **Step 5: Run frontend helper tests and commit**

Run:

```bash
npm test -- src/pages/BcnHome/lib/botAccess.test.ts --runInBand
npx prettier --check src/pages/BcnHome/lib/botAccess.ts \
  src/pages/BcnHome/lib/botAccess.test.ts src/shell/extension.ts src/shell/types.ts
```

Expected: all tests and formatting checks pass.

Commit only production-ready files; do not commit the local SHA preview:

```bash
git add src/frontend/src/pages/BcnHome/lib/botAccess.ts \
  src/frontend/src/pages/BcnHome/lib/botAccess.test.ts \
  src/frontend/src/shell/extension.ts src/frontend/src/shell/types.ts
git commit -m "feat(frontend): generate multi-profile Hermes commands"
```

---

### Task 3: Reuse One Hermes Configuration Form In Both Entry Points

**Files:**
- Create: `src/frontend/src/pages/BcnHome/components/HermesBotConfigFields.tsx`
- Modify: `src/frontend/src/pages/BcnHome/components/AccessSection.tsx`
- Modify: `src/frontend/src/pages/GroupChat/components/AddBotGuideModal.tsx`
- Test: `src/frontend/src/pages/BcnHome/lib/botAccess.test.ts`

**Interfaces:**
- Consumes: `HermesBotConfigValidation` and `renderBotAccessCommand` from Task 2.
- Produces: presentational component `HermesBotConfigFields`.

- [ ] **Step 1: Add a failing presentation-contract test**

Export shared copy from `botAccess.ts` and assert it remains explicit:

```ts
expect(HERMES_MULTI_PROFILE_NOTICE).toBe(
  '支持接入多个 Hermes Bot。每个 Bot 必须使用独立 Profile；重复使用同一 Profile 将恢复原 Bot。',
);
```

Expected: FAIL because the constant does not exist.

- [ ] **Step 2: Add the shared field component**

Create a controlled component with stable IDs and no internal business logic:

```tsx
interface HermesBotConfigFieldsProps {
  idPrefix: string;
  botName: string;
  profile: string;
  validation: HermesBotConfigValidation;
  onBotNameChange: (value: string) => void;
  onProfileChange: (value: string) => void;
}

export const HermesBotConfigFields: React.FC<HermesBotConfigFieldsProps> = ({
  idPrefix,
  botName,
  profile,
  validation,
  onBotNameChange,
  onProfileChange,
}) => (
  <div className="grid gap-3 sm:grid-cols-2">
    <label className="text-xs font-medium text-[#52606d]" htmlFor={`${idPrefix}-bot-name`}>
      Bot 名称
      <input
        id={`${idPrefix}-bot-name`}
        value={botName}
        onChange={(event) => onBotNameChange(event.target.value)}
        placeholder="例如 Hermes Reviewer"
        className="mt-1 h-9 w-full rounded-md border border-[#d9e0ea] bg-white px-3 text-sm text-[#1a2332] outline-none focus:border-[#1d4ed8]"
      />
      {validation.botNameError && <span className="mt-1 block text-xs text-[#dc2626]">{validation.botNameError}</span>}
    </label>
    <label className="text-xs font-medium text-[#52606d]" htmlFor={`${idPrefix}-profile`}>
      Profile 名称
      <input
        id={`${idPrefix}-profile`}
        value={profile}
        onChange={(event) => onProfileChange(event.target.value)}
        placeholder="例如 avernet-hermes-2"
        className="mt-1 h-9 w-full rounded-md border border-[#d9e0ea] bg-white px-3 font-mono text-sm text-[#1a2332] outline-none focus:border-[#1d4ed8]"
      />
      {validation.profileError && <span className="mt-1 block text-xs text-[#dc2626]">{validation.profileError}</span>}
    </label>
  </div>
);
```

- [ ] **Step 3: Integrate the landing-page access section**

Add controlled state, memoized validation, the notice, and fields only for the
Hermes manual card. Render commands with `renderBotAccessCommand`. Disable copy
when the token is loading or Hermes config is invalid:

```ts
const hermesConfig = { botName: hermesBotName, profile: hermesProfile };
const hermesValidation = validateHermesBotConfig(hermesConfig);
const hermesManual = selectedEngine.id === 'hermes' && item.id === 'manual';
const command = renderBotAccessCommand(
  item.template,
  token ?? TOKEN_PLACEHOLDER,
  hermesManual ? hermesConfig : undefined,
);
```

- [ ] **Step 4: Integrate the workbench modal**

Use the same state, notice, validation, component, and rendering path in
`AddBotGuideModal`. Disable its copy button when `!token` or the selected
Hermes manual method is invalid. Keep values while switching access methods or
engines during one modal lifetime.

- [ ] **Step 5: Format, test, and commit**

Run:

```bash
npm test -- src/pages/BcnHome/lib/botAccess.test.ts --runInBand
npx prettier --check src/pages/BcnHome/components/HermesBotConfigFields.tsx \
  src/pages/BcnHome/components/AccessSection.tsx \
  src/pages/GroupChat/components/AddBotGuideModal.tsx
npm run ci
```

Expected: Jest, Prettier, and frontend lint pass.

Commit:

```bash
git add src/frontend/src/pages/BcnHome/components/HermesBotConfigFields.tsx \
  src/frontend/src/pages/BcnHome/components/AccessSection.tsx \
  src/frontend/src/pages/GroupChat/components/AddBotGuideModal.tsx \
  src/frontend/src/pages/BcnHome/lib/botAccess.test.ts
git commit -m "feat(frontend): expose multiple Hermes bot onboarding"
```

---

### Task 4: Verify The Complete Workflow And Publish PR 102

**Files:**
- Verify: all files changed in Tasks 1-3.
- Keep untracked: `.codegraph/`.

**Interfaces:**
- Consumes: installer and frontend contracts from Tasks 1-3.
- Produces: updated PR #102 and a working local `8010` preview.

- [ ] **Step 1: Run focused and full automated verification**

Run:

```bash
/bin/bash -n src/bcs/docs/install-instructions/install-hermes.sh
~/.hermes/hermes-agent/venv/bin/python -m unittest tests.test_cli
npm test -- src/pages/BcnHome/lib/botAccess.test.ts --runInBand
npm run ci
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 2: Verify installer behavior with temporary homes**

Use fake Hermes/profile homes to prove these exact outcomes without consuming
a real registration token:

```text
missing profile + --create-profile -> profile created
same profile + same stored bot_name -> resume allowed
same profile + different stored bot_name -> actionable failure
```

Expected: no temporary credentials or processes remain after the checks.

- [ ] **Step 3: Verify both frontend surfaces in the browser**

At `http://127.0.0.1:8010/` and the workbench `接入 Bot` modal:

1. Select Hermes.
2. Confirm the multi-profile notice is visible.
3. Confirm empty fields disable copy and show validation after interaction.
4. Enter `Hermes Reviewer` and `avernet-hermes-reviewer`.
5. Confirm the generated command contains shell-quoted `--bot-name`,
   `--profile`, and `--create-profile` exactly once.
6. Confirm OpenClaw output is unchanged.
7. Check desktop and mobile screenshots for clipping or overlap.

- [ ] **Step 4: Restore the local preview override after production commit**

Keep the committed template on `refs/heads/dev`. After the final commit/push,
set the local working copy to the new PR commit SHA for the installer, connector,
and guide URLs, then rerun the focused frontend test. Leave only that preview
change and `.codegraph/` uncommitted.

- [ ] **Step 5: Push and confirm PR head**

Push through the configured local proxy so the repository pre-push gate runs:

```bash
git -c http.proxy=http://127.0.0.1:7897 \
  -c https.proxy=http://127.0.0.1:7897 \
  push fork design/hermes-bcn-entry
HTTPS_PROXY=http://127.0.0.1:7897 \
  gh pr view 102 --repo inclusionAI/Avernet \
  --json url,state,headRefOid,headRefName,baseRefName
```

Expected: pre-push BCS and frontend gates pass, PR #102 remains open, and its
head OID equals local `HEAD`.
