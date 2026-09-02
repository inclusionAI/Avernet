# @avernet/clawevolve

ClawEvolve server module extracted from the locked ClawWeb source revision listed
in `COPY_MANIFEST.tsv`. The package keeps the original routes, repositories,
services, schema migrations, and tests while moving environment-specific
capabilities behind host-injected contracts.

This phase intentionally excludes `clawevolve-skill`.

## Embedded module boundary

ClawWeb injects its already-created Insight repositories, governance provider,
and optional Evidence reader through `ClawInsightInternalApi`. Clawevolve does
not create a second Insight runtime. The module returns `ClawEvolveInternalApi`
so the existing Insight Router can create Evolve tasks without mounting a
second HTTP bridge. This boundary-only step intentionally preserves the copied
business logic, tests, schema, and frontend consumer code until Pre behavior is
verified.
