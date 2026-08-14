# REL20260717 rebase-to-dev pre-push gate

## Goal

Allow the rebased `REL20260717` branch to pass the repository's mandatory local
Python static-analysis gate without changing production behavior or test intent.

## Scope

- Replace a boolean equality assertion with an identity assertion.
- Remove unresolved helper return annotations in governance tests where the
  implementation class is deliberately imported only inside the helper body.
- Keep all runtime test behavior unchanged.

## Validation

1. Run the affected bot-public and governance test modules.
2. Run `scripts/ci/python_sast_local.sh` with the current `origin/dev` base and
   branch head.
3. Re-run the repository pre-push gate through the normal `git push` path.
