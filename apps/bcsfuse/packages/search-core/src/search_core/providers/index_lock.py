"""Concurrent Server reads, exclusive mutations; Local keeps its existing mutex."""

from contextlib import contextmanager
from threading import Condition, RLock, get_ident


class IndexLock:
    def __init__(self, *, concurrent_reads: bool):
        self._concurrent = concurrent_reads
        self._condition = Condition(RLock())
        self._writer = None
        self._depth = 0
        self._readers = 0
        self._waiting_writers = 0

    def __enter__(self):
        with self._condition:
            identity = get_ident()
            if self._writer == identity:
                self._depth += 1
                return self
            self._waiting_writers += 1
            try:
                while self._writer is not None or self._readers:
                    self._condition.wait()
                self._writer, self._depth = identity, 1
            finally:
                self._waiting_writers -= 1
        return self

    def __exit__(self, *args):
        with self._condition:
            self._depth -= 1
            if not self._depth:
                self._writer = None
                self._condition.notify_all()

    @contextmanager
    def read(self):
        if not self._concurrent:
            with self:
                yield
            return
        with self._condition:
            while self._writer != get_ident() and (
                self._writer is not None or self._waiting_writers
            ):
                self._condition.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()
