"""Local Skill storage address contract, independent of repository synchronization.

The selected root is both the package locator prefix and the address passed to
existing device-file adapters. A provider may select an Engine-owned Legacy
root; it must not migrate locators, modify files, or change Pool state.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class LocalSkillStorageResolver(Protocol):
    def resolve_root(self, runtime_engine: str, configured_root: Path) -> Path:
        """Select a Legacy upload root; preserve unsupported runtime policies.

        Called only after existing Pool resolution has declined ownership.
        The caller retains separate locator and device-I/O adapters, including
        Teclaw addressing and enterprise Engine host-prefix translation.
        """
        ...
