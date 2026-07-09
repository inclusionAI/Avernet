"""Corp-free test helpers that ship with the community package.

These utilities are shared by the test-suite bootstraps of both trees
(``tests/community`` and ``tests/corp``). They live in the shipped
community package — not under ``tests/`` — for two reasons:

1. After the community/corp split there is no shared root ``conftest.py``
   (pytest applies conftests by directory ancestry, so a community-owned
   root conftest would stop covering the corp tree once community is
   extracted). Each subtree conftest is self-contained and imports the
   shared helpers from here instead.
2. Post-extraction the corp repo consumes community as an installed
   dependency; importing these helpers as ``agentclaw.community.testing``
   keeps working (corp → community is a permitted dependency direction),
   whereas a ``tests/``-level import would not.

Dev-only third-party imports (``respx`` / ``httpx`` / ``pytest``) are made
**lazily inside functions**, never at module import, so importing this
package in a production runtime (which has no dev deps) never fails.
"""
