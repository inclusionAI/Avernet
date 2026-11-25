"""Bounded parallel scans retain serial filtering/order/total and request scope."""

from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event, Lock, get_ident

import pytest

from agentclaw.community.core.bot_public.services import catalog_pagination as scan
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope, get_current_avernet_tenant


def _rows(size):
    return [{"bot_id": str(i), "bot_type": "service" if i % 3 else "personal",
             "online_available": i % 2 == 0} for i in range(size)]


@pytest.mark.parametrize("page", [1, 2, 20, 50])
def test_out_of_order_reads_keep_exact_window_total_and_context(monkeypatch, page):
    gate = BoundedSemaphore(2)
    monkeypatch.setattr(scan, "_PARALLEL_SCANS", gate)
    rows = _rows(745)
    lock, third_done = Lock(), Event()
    probe = ContextVar("catalog_test_context", default="missing")
    active = peak = 0
    finished, observed = [], []

    def read_page(*, page, page_size):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            observed.append((get_current_avernet_tenant(), probe.get(), page))
        try:
            if page == 2:
                assert third_done.wait(3), "The second page must overlap the third"
            with lock:
                finished.append(page)
            if page == 3:
                third_done.set()
            offset = (page - 1) * page_size
            return {"total": len(rows), "items": rows[offset:offset + page_size]}
        finally:
            with lock:
                active -= 1

    token = probe.set("trace-a")
    try:
        with avernet_tenant_scope("catalog-tenant-a"):
            result = scan.online_catalog_page(read_page, page=page, page_size=24)
    finally:
        probe.reset(token)
    eligible = [row for row in rows if row["bot_type"] != "service" or row["online_available"]]
    assert result == {"total": len(eligible), "items": eligible[(page - 1) * 24:page * 24]}
    assert peak == 2
    assert active == 0
    assert finished.index(3) < finished.index(2)
    assert sorted(item[2] for item in observed) == list(range(1, 9))
    assert all(item[:2] == ("catalog-tenant-a", "trace-a") for item in observed)
    assert gate.acquire(blocking=False) and gate.acquire(blocking=False)
    assert not gate.acquire(blocking=False)
    gate.release()
    gate.release()


def test_busy_process_falls_back_to_serial_without_creating_more_workers(monkeypatch):
    gate = BoundedSemaphore(1)
    gate.acquire()
    monkeypatch.setattr(scan, "_PARALLEL_SCANS", gate)
    thread_ids = []

    def read_page(*, page, page_size):
        thread_ids.append(get_ident())
        return {"total": 245, "items": _rows(245)[(page - 1) * page_size:page * page_size]}

    try:
        result = scan.online_catalog_page(read_page, page=1, page_size=24)
    finally:
        gate.release()
    assert result["items"]
    assert thread_ids == [get_ident()] * 3


def test_worker_failure_does_not_return_partial_data_or_leak_scan_slot(monkeypatch):
    gate = BoundedSemaphore(1)
    monkeypatch.setattr(scan, "_PARALLEL_SCANS", gate)
    failure = RuntimeError("read failed")

    def read_page(*, page, page_size):
        if page == 2:
            raise failure
        return {"total": 300, "items": _rows(1)}

    with pytest.raises(RuntimeError) as raised:
        scan.online_catalog_page(read_page, page=1, page_size=24)
    assert raised.value is failure
    assert gate.acquire(blocking=False)
    gate.release()


@pytest.mark.parametrize("totals", [(200, 350, 350, 350), (350, 100)])
def test_observed_growth_and_shrink_keep_serial_stop_rules(totals):
    def read_page(*, page, page_size):
        total = totals[min(page - 1, len(totals) - 1)]
        return {"total": total, "items": [{"bot_id": str(page)}]}

    result = scan.online_catalog_page(read_page, page=1, page_size=24)
    assert result["total"] == len(totals)


def test_empty_filtered_bcs_page_stops_scan():
    calls = []

    def read_page(*, page, page_size):
        calls.append(page)
        return {"total": 500, "items": []}

    assert scan.online_catalog_page(read_page, page=1, page_size=24) == {"total": 0, "items": []}
    assert calls == [1]


def test_first_pages_share_a_process_read_budget(monkeypatch):
    monkeypatch.setattr(scan, "_PAGE_READS", BoundedSemaphore(2))
    lock, full, release = Lock(), Event(), Event()
    active = peak = 0

    def read_page(**kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                full.set()
        try:
            assert release.wait(3)
            return {"total": 1, "items": [{"bot_id": "one"}]}
        finally:
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(scan.online_catalog_page, read_page, page=1, page_size=24) for _ in range(6)]
        try:
            assert full.wait(3)
        finally:
            release.set()
        assert all(future.result()["total"] == 1 for future in futures)
    assert peak == 2
    assert active == 0
