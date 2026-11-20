"""Protocol conformance suites for task_experience.

Each module exposes one ``assert_xxx_invariants(make_xxx)`` async function
documenting the canonical Protocol invariants (sentinel behaviour, type
signatures, disabled / empty paths). The community instance passes via the
``make_xxx`` builders wired in
``tests/community/core/task/task_experience/test_community_conformance.py``;
real corp implementations (OCB ``CorpTaskExperienceIntegration`` providers)
re-import these assertions to run them against their respective instances.

These suites are intentionally small. They check the contract surface,
not the algorithm — algorithm-level cases live in the per-feature unit
tests under the task_experience test directory.
"""

from __future__ import annotations
