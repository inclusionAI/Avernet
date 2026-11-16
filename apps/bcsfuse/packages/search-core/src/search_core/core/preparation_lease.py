"""Renew a DB-time-based preparation lease while a synchronous model is running."""

from threading import Event, Thread


class PreparationLease:
    def __init__(self, repository, source_id, revision, index_key, token, seconds):
        self.repository = repository
        self.args = (source_id, revision, index_key, token, seconds)
        self.seconds = seconds
        self.stop, self.lost = Event(), Event()
        self.thread = Thread(
            target=self._renew, daemon=True, name="index-prepare-lease"
        )

    def _renew(self):
        while not self.stop.wait(self.seconds / 3):
            try:
                if not self.repository.renew_preparation(*self.args):
                    self.lost.set()
                    return
            except Exception:  # noqa: BLE001 -- uncertainty loses publication authority
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
