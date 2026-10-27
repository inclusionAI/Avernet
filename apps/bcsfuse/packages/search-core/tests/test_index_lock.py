"""Reads may overlap only in Server mode; mutations remain exclusive."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from search_core.providers.index_lock import IndexLock


@pytest.mark.parametrize("concurrent", [False, True])
def test_writer_waits_for_reader_and_can_reenter(concurrent):
    lock = IndexLock(concurrent_reads=concurrent)
    attempting, entered = Event(), Event()

    def write():
        attempting.set()
        with lock, lock, lock.read():
            entered.set()

    with ThreadPoolExecutor(1) as pool:
        with lock.read():
            future = pool.submit(write)
            assert attempting.wait(2)
            assert not entered.wait(0.05)
        future.result(timeout=2)
        assert entered.is_set()
    with pytest.raises(ValueError), lock:
        raise ValueError("release after error")
    with lock.read():
        pass
