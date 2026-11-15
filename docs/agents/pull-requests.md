# Pull requests

Read before preparing a commit or PR. Paths below are relative to the repository
root. Use the verified authoritative GitHub remote, not a fetch mirror. Target
`dev` unless the task specifies another branch; contribute from that baseline
and rebase rather than merging release history into it. Keep push gates enabled.

## Pull Request Conventions

A pull request is read by external contributors and reviewers who do not share
your context, and its title becomes the commit message when the PR is squash
merged. Both must carry meaning on their own.

### Title

Use `<type>(<scope>): <concise outcome>`:

```text
feat(backend): add whitelist observed state
fix(bcs): reject routing updates for unknown bot ids
docs(arch): document plugin protocol conformance shape
```

| Type | Use for |
| --- | --- |
| `feat` | New functionality |
| `fix` | Bug fix |
| `refactor` | Restructuring with no external behavior change |
| `docs` | Documentation |
| `test` | Tests |
| `ci` | CI/CD |
| `build` | Build system or dependencies |
| `perf` | Performance improvements |
| `chore` | Other maintenance |

The required scope is the module or area you touched, such as `backend`,
`baas`, `engine`, `bcs`, `frontend`, `gateway`, `arch`, or `ci`. The outcome
describes what the change accomplishes, not which files moved.

Do not use vague or context-free titles such as `fix bug`, `update code`,
`sync`, a bare branch name, or an issue number with no summary.

### Description

Use these sections, in this order:

```markdown
## Problem
## Solution
## Validation
## Compatibility and risk (optional)
## Spec (optional)
## Related issues (optional)
```

- **Problem** — the observed defect, gap, or requirement, and why it matters.
- **Solution** — the approach taken, and the alternatives rejected when the
  choice is not obvious.
- **Validation** — the tests, gates, and manual checks you actually ran, with
  their results. State explicitly what you could not run and why.
- **Compatibility and risk** — contract, schema, config, or migration impact,
  and the rollback path.
- **Spec** — the contract or design document this change implements.
- **Related issues** — issues this closes or relates to.

`.github/pull_request_template.md` holds this skeleton. Fill in every required
section; delete the optional sections that do not apply rather than leaving
them empty.
