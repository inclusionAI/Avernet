# Multi-Hermes BCN Onboarding Design

## Context

The BCN access section exposes Hermes as a first-class engine, but its manual
command does not identify a Hermes profile. The installer therefore selects the
default Hermes home. Once that home contains a valid BCN session, entering a
different bot name does not create another bot; the existing bot is resumed.

This is correct for idempotent recovery but is not clear in the UI. A user can
reasonably expect that entering `hermes4` creates a fourth Hermes bot while the
command actually reconnects the bot already stored in the default profile.

## Goal

Make multi-Hermes onboarding explicit and functional:

- state that multiple Hermes bots are supported;
- require one Hermes profile per BCN bot;
- collect a bot display name separately for manual and bot-assisted onboarding;
- derive a safe, stable Hermes profile from each bot display name;
- create a missing profile by cloning the default Hermes profile; and
- reject a profile that is already registered under a different bot name.

The resulting command must remain safe to copy, must keep the human token out
of process arguments, and must be idempotent when rerun for the same bot and
profile.

## Non-goals

This change does not add a profile inventory or process-management dashboard,
rename registered BCN bots, delete profiles, replace existing credentials, or
change OpenClaw onboarding.

## Frontend Experience

When Hermes is selected, the access section shows a compact notice above the
method cards:

> Multiple Hermes bots are supported. Avernet automatically creates an
> isolated Profile from the Bot name; reusing the same name resumes that Bot.

Each Hermes method adds its own **Bot name** input before its generated
command. The manual and bot-assisted values are independent and remain intact
when the user switches methods in the workbench modal. The landing page shows
one field in each method card.

There is no editable Profile field. The frontend derives the profile from the
trimmed Bot name and passes it to the installer or bot-assisted instruction.
The notice explains that Avernet creates this isolated profile automatically.

The copy button is disabled while that method's Bot name is empty. Inline
validation uses concise field-level text; invalid values are never inserted
into a shell command or bot instruction.

The landing-page `AccessSection` and the workbench `AddBotGuideModal` render
the same small Bot-name field and consume the same profile derivation,
validation, and command-rendering helpers. The two entry points must not drift
in copy, defaults, or generated arguments.

The bot-assisted Hermes method remains available and receives the same
multi-profile notice. Its generated instruction embeds the same installer
command as self-service onboarding, including the selected Bot name and derived
Profile, so Hermes does not improvise another BCN integration path.

## Derived Profile Names

Profile generation is deterministic and frontend-owned:

1. Trim the Bot name and normalize it with Unicode NFKD.
2. Lowercase it, remove combining marks, replace every run outside
   `[a-z0-9]` with `-`, and trim leading or trailing `-` characters.
3. When an ASCII slug remains, prefix it with `avernet-` and truncate the slug
   so the complete Profile is at most 64 characters. Remove a trailing `-`
   after truncation.
4. When no ASCII slug remains, calculate a stable 32-bit FNV-1a hash over the
   trimmed JavaScript string's UTF-16 code units: start at `0x811c9dc5`, XOR
   each `charCodeAt` value, and multiply with `Math.imul` by `0x01000193`.
   Use `avernet-bot-<8 lowercase hex digits>`. An empty Bot name is invalid and
   never reaches this fallback.

For example, `Hermes Reviewer` becomes `avernet-hermes-reviewer`. The same Bot
name always produces the same Profile. Pure slug conversion can map distinct
names to one Profile; the installer's existing stored-bot-name conflict guard
then rejects the second name with an actionable error instead of reconnecting
the wrong bot.

## Command Rendering

Both Hermes templates gain placeholders for the bot and derived profile names.
The shared access helper renders all placeholders:

```text
{token}
{bot_name}
{profile}
```

The token remains single-quoted and piped over standard input. Bot and profile
values are shell-escaped by one shared helper before template substitution.
Profile validation prevents path traversal and option injection; bot names may
contain spaces or non-ASCII display text after shell escaping.

The rendered manual installer invocation includes:

```text
--bot-name <escaped bot name>
--profile <validated profile>
--create-profile
```

OpenClaw templates continue to render only `{token}` and retain their current
behavior.

The bot-assisted instruction includes the escaped display name and derived
profile in the exact `install-hermes.sh` command. That command uses
`--create-profile` so a new derived profile is cloned from `default` before
registration.

## Installer Semantics

`install-hermes.sh` adds `--create-profile`. The option is valid only with
`--profile`.

Profile handling occurs before the existing configured-profile preflight:

1. If the named profile does not exist and `--create-profile` is present, run
   `hermes profile create <profile> --clone-from default`.
2. If creation fails or does not produce `config.yaml`, stop before BCN
   registration.
3. If the profile exists, never modify or clone over it.
4. If a valid BCN session exists and its stored `bot_name` equals the requested
   name, resume it idempotently.
5. If a valid session contains a non-empty, different stored `bot_name`, fail
   with an error that names the profile and existing bot and instructs the user
   to choose a different profile. A legacy session without `bot_name` keeps the
   existing resume behavior.

The existing `--replace` flow remains explicit and separate. It is not used to
turn one profile into a different bot.

Profile creation is recorded in the resume command through
`--create-profile`, so an interrupted install remains resumable. Registration
credentials keep their existing atomic and owner-only persistence.

## Error Behavior

The installer fails before registration for:

- `--create-profile` without `--profile`;
- an invalid, default, or Hermes-reserved profile slug;
- a missing profile when automatic creation was not requested;
- profile creation that does not yield a configured profile; or
- an existing valid BCN session whose stored bot name differs from the
  requested name.

The frontend generator always produces a non-reserved profile matching
`[a-z0-9][a-z0-9_-]{0,63}`. Installer-side validation remains authoritative for
direct CLI users and protects against future frontend drift.

## Testing

Frontend unit tests cover:

- Hermes multi-instance copy and field metadata;
- independent manual and bot-assisted Bot-name state;
- ASCII, accented, long, and non-ASCII-only profile derivation;
- shell-safe bot-name rendering;
- manual command rendering with all three installer flags;
- bot-assisted instruction rendering with Bot and Profile values;
- copy disabled for invalid values; and
- unchanged OpenClaw command rendering.

Installer tests cover:

- creating a missing profile from `default`;
- leaving an existing configured profile untouched;
- rejecting `--create-profile` without a profile;
- resuming the same profile/name pair without registration;
- rejecting a profile/name mismatch before registration; and
- preserving `--create-profile` in an interrupted-install resume command.

The existing Hermes CLI suite, frontend access tests, shell syntax check, and
repository pre-push gates remain required.

## Acceptance Criteria

1. Manual and bot-assisted Hermes onboarding each require only a Bot name and
   generate their own command or instruction.
2. A Bot name deterministically produces a valid Hermes Profile without an
   editable Profile field, including for non-ASCII-only names.
3. Rerunning a method with the same Bot name resumes the existing bot.
4. Reusing a derived Profile that already belongs to a different Bot name
   produces an actionable error instead of silently reconnecting the old bot.
5. The UI states that an isolated Profile is generated automatically.
6. Registration tokens are never exposed in installer arguments or logs.
