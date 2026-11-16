# Agent evaluation maintenance

- This is the public authority for reusable deterministic gates and rubric contracts.
- No internal endpoints, identities, dataset contents, tokens or machine-specific paths.
- Add behavioral regression tests for contract changes; retain fail-closed semantics.
- Do not turn lexical overlap, fixture labels or source hashes into semantic acceptance.
- Do not modify the existing diagnosis skill's evaluator as an incidental migration fix.
- Keep each source file below 1,000 lines; run `python -m unittest discover -s tests -v` here.
