# Codex GitHub automation

`codex-review-autofix.yml` listens for new P1 or P2 inline review comments from
`chatgpt-codex-connector[bot]` only when the pull request author is
`FreddieSun` and its head branch belongs to this repository. It asks Codex for
the smallest applicable fix, then pushes a commit only when Codex leaves a
non-empty, non-workflow diff.

GitHub Actions workflows are repository-scoped. GitHub creates a run record
for every matching review-comment event before evaluating the job condition, so
other contributors can see a skipped run in the Actions UI. Their pull requests
never invoke Codex, receive the personal API key, modify code, or consume the
key's usage. A fully private event-driven workflow requires a separately hosted
webhook receiver owned by FreddieSun rather than a workflow stored in this
repository.

## Required secret

Create a personal OpenAI Project API key and add it as the repository Actions
secret `FREDDIESUN_OPENAI_API_KEY`.

The secret value is not displayed to collaborators. However, anyone who can
change a trusted workflow on the base branch can make a workflow use the
secret, so this automation must remain limited to protected branches and
trusted maintainers. The workflow excludes pull requests from forks and does
not provide GitHub credentials to the Codex step.

## Cloud environment

The GitHub Action runs on a GitHub-hosted runner and does not use the Codex
Cloud environment. Configure a separate Cloud environment only for interactive
`@codex` PR tasks. Its setup should install the repository dependencies needed
for the modules you ask Codex to modify; do not put API keys in the Cloud
environment.
