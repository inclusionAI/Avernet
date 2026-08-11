# Avernet Star Daily Skill snapshot

These scripts are vendored from `carolynli/efficiency` at commit
`52c9e6d34efd40e6d808a1c086d3c75b5004e687`, directory
`skills/avernet-star-daily/scripts/`.

- `avernet_star_stats.py` SHA-256:
  `b2f53373ffb08efcb8f87db7262ceb941e97c187da8b2c6f099bc28f0b3ca283`
- `generate_star_image.py` SHA-256 before the checked-in rendering overlay:
  `333a1801475d79b21011beb305f3ea9842721242291860a7f71ddff341412f74`

The workflow verifies both hashes before applying
`scripts/ci/avernet_star_growth.patch`. Vendoring keeps the GitHub-hosted
workflow independent of private cross-repository credentials while preserving
the pinned Skill implementation and provenance.
