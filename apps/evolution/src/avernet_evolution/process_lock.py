"""Single local-volume authority. SQLite/WAL is not a multi-replica service."""

import fcntl
from pathlib import Path


class ProcessLock:
    def __init__(self, database: Path):
        self.file = Path(f"{database}.lock").open("a")  # noqa: SIM115 - lock owns this handle until close()
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("another Evolution process owns this database") from None

    def close(self):
        self.file.close()
