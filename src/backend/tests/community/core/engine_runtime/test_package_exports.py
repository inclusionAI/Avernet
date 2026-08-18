"""The package's re-exports stay in step with the errors module.

``core/engine_runtime/__init__.py`` restates every error name twice — once to
import it, once in ``__all__`` — and callers reach them by the package path.
That is a second inventory beside ``errors.__all__``, and nothing else notices
when the two disagree: adding an error and forgetting the package leaves ruff
clean, the ordering pin in ``test_responses.py`` green (it reads the *errors*
module), and the first ``from …core.engine_runtime import NewError`` failing at
import time.
"""

from __future__ import annotations

import agentclaw.community.core.engine_runtime as package
import agentclaw.community.core.engine_runtime.errors as errors


def test_every_error_is_reachable_from_the_package():
    missing = [
        name
        for name in errors.__all__
        if getattr(package, name, None) is not getattr(errors, name)
    ]
    assert not missing, (
        f"{missing} are exported by errors.py but not re-exported by the "
        "package; import them in __init__.py and list them in its __all__"
    )


def test_the_package_all_lists_what_it_re_exports():
    missing = [name for name in errors.__all__ if name not in package.__all__]
    assert not missing, f"{missing} are importable from the package but absent from __all__"
