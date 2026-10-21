# Bailian engine smoke

This opt-in GitHub Actions workflow configures OpenClaw, DeepSeek Harness,
Claude Code and Codex with pay-as-you-go Bailian `qwen3.8-flash`. QwenWork is
excluded. Engine execution is restricted to GitHub-hosted runners; local
validation only exercises the configuration generator and test doubles.

## Configure GitHub

In repository **Settings → Secrets and variables → Actions** create:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `BAILIAN_API_KEY` | Your pay-as-you-go API key; enter it directly in GitHub |
| Variable | `BAILIAN_OPENAI_BASE_URL` | Your workspace's HTTPS URL ending in `/compatible-mode/v1` |

The key must belong to the endpoint's region/workspace and have model access.
The Claude endpoint is derived from the same origin with `/apps/anthropic`.
Do not put a key in a workflow input, repository file, PR, or chat message.

Run **Actions → Bailian Engine Smoke → Run workflow** after the workflow
exists on the repository default branch. For initial branch validation the
workflow also runs on changes to its files on `codex/bailian-engine-smoke`.
If that run fails because settings were missing, configure them and use
**Re-run all jobs**. A workflow with only `workflow_dispatch` cannot initially
be dispatched until GitHub knows it on the default branch.

## What is checked

Each matrix job resolves npm's `latest` tag once, installs that exact version,
and records the package/version and repository commit. Prereleases are tested
only when the publisher assigns them to `latest`. No existing engine profile
is reused. The jobs use Node 24 and Python 3.12 on Ubuntu, two at a time.

OpenClaw and DeepSeek Harness use Chat Completions, Claude Code uses Anthropic
Messages, and Codex uses Responses. Only the engine invocation receives the
model credential (apart from a presence check); package installation does not.

One paid task asks the engine to read a file containing a random marker that
is absent from the prompt and return its content. The invocation has a
240-second process-group timeout. This checks a real model/tool round trip;
it does not certify BCS onboarding, BCN events, routing, cancellation, or
multi-turn sessions. Reports explicitly set `bcs_certified: false`. Add BCS
end-to-end cases and a known-good engine comparison before using these results
as a daily BCS readiness gate. No daily schedule is enabled at this stage.

`PASS` means the engine exited successfully and returned the marker. Missing
configuration, setup errors, timeouts and detected auth/quota errors are
`UNKNOWN`; other engine failures are `FAIL`. Neither is silently skipped.
Error classification is diagnostic, not proof of an engine regression.
Artifacts contain only scoped result metadata and resolved versions, not raw
engine logs, sessions, prompts or configuration. A failed invocation prints a
bounded diagnostic excerpt to the job log after redacting the configured key
and common token formats. Runtime state is deleted.
An execution timeout is not a hard monetary limit: provider retries and model
reasoning can still incur costs; use the provider's account limits as needed.

## Offline validation

```sh
python3 -m unittest discover -s singlebox/compat/bailian/tests -v
```

Sources: [Bailian Claude Code](https://help.aliyun.com/zh/model-studio/claude-code),
[Bailian Codex](https://help.aliyun.com/zh/model-studio/codex),
[Codex configuration](https://developers.openai.com/codex/config-advanced/),
[DSH providers](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md).
