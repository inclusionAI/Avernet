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
- collect the bot display name and profile name before command generation;
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

> Multiple Hermes bots are supported. Each bot must use a separate profile;
> reusing a profile resumes the bot already registered in it.

The manual-access card adds two inputs before the generated command:

- **Bot name**: the BCN display name, required and trimmed;
- **Profile name**: a named Hermes profile slug, required and validated against
  Hermes' native `[a-z0-9][a-z0-9_-]{0,63}` format.

The profile field starts empty and shows `avernet-hermes-2` as an example
placeholder. `default` and Hermes-reserved profile names are rejected because
this flow is specifically for creating an independently named instance.

The copy button is disabled while either value is invalid. Inline validation
uses concise field-level text; invalid values are never inserted into a shell
command.

The bot-assisted Hermes method remains available and receives the same
multi-profile notice. This MVP does not attempt to send form values through an
unstructured prompt.

## Command Rendering

The Hermes manual template gains placeholders for the bot and profile names.
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

The rendered installer invocation includes:

```text
--bot-name <escaped bot name>
--profile <validated profile>
--create-profile
```

OpenClaw templates continue to render only `{token}` and retain their current
behavior.

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

The frontend prevents the first two cases for copied commands. Installer-side
validation remains authoritative for direct CLI users.

## Testing

Frontend unit tests cover:

- Hermes multi-instance copy and field metadata;
- profile validation;
- shell-safe bot-name rendering;
- command rendering with all three installer flags;
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

1. A user can copy two commands with different profile names and register two
   independently running Hermes bots.
2. Rerunning a command with the same bot and profile resumes the existing bot.
3. Reusing that profile with a different bot name produces an actionable error
   instead of silently reconnecting the old bot.
4. The UI explicitly states the one-profile-per-bot rule.
5. Registration tokens are never exposed in installer arguments or logs.
