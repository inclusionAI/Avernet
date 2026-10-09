from __future__ import annotations

from pathlib import Path


def test_local_package_modules_do_not_duplicate_engine_layout_roots() -> None:
    skills_root = Path(__file__).parents[2] / "core" / "skills"
    modules = [
        skills_root / "local_package.py",
        skills_root / "local_package_application.py",
    ]

    for module in modules:
        source = module.read_text(encoding="utf-8")
        assert "/home/admin" not in source
        assert "/skills-local" not in source
