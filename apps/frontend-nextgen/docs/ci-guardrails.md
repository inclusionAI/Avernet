# Frontend CI guardrails

`npm run ci:guardrails` validates rules that can be reproduced from the public
Avernet checkout:

- source-file size thresholds;
- `Component → Hook → Service → API Controller` import boundaries;
- business components must not use a native `<button>`;
- business components must use `Tooltip` rather than a `title` attribute for
  explanatory UI.

`npm run ci` runs this guard first, then type checking, linting, unit tests and
the production build. The `Frontend Nextgen / Open Core frontend checks` job in
`.github/workflows/frontend-nextgen.yml` is the status check to require for the
`dev` branch in GitHub branch protection.

Internal-only composition is intentionally outside this script. OCB's PR
pipeline runs `apps/frontend-nextgen/scripts/ci-internal.sh`, which assembles
the OCB shell with the pinned Avernet source and then runs the same frontend CI
against the assembled project. A scheduled or manually dispatched
cross-repository verification must live in OCB, where the private repository
credentials and internal build configuration are available.
