"""Bounded, drained fan-out for the per-file I/O every device backend makes.

Device filesystems address one file per call — a read, a write, a delete is a
round trip to the container. Issuing them in a loop made an N-file operation cost
``N × round_trip``, which dominates every Skill / Skill Set request that touches a
package: the upload paths, the read-back verifies, the restore and quarantine
copies, and the switcher's directory sweep.

:func:`gather_device_io` is the one substitution those loops make. It is not a
plain ``gather``: it is bounded, and it drains before it lets anything out — the
two properties that make it safe to swap in for a sequential loop whose caller
compensates on failure. Both are spelled out on the function.
"""
from __future__ import annotations

import asyncio


# Every package file is one device round trip. Issuing them sequentially made a
# package cost ``file_count × round_trip``, which dominates upload time for the many
# small files a skill package is made of. Fan them out instead — but bounded: device
# filesystems run their blocking transport through ``asyncio.to_thread``, whose
# default executor (``min(32, cpu_count + 4)`` threads) is shared with every other
# caller in the process, so an unbounded ``gather`` over a large package would starve
# unrelated work.
DEVICE_IO_CONCURRENCY = 8


async def gather_device_io(
    coroutines: list, *, return_exceptions: bool = False
) -> list:
    """Run per-file device I/O concurrently, bounded, and drain before returning.

    Every coroutine is awaited to completion even after one fails, then the first
    failure *in input order* is re-raised. Draining is what makes the fan-out safe
    to substitute for the sequential loop: a caller that sees ``write`` fail treats
    the package as failed and immediately ``delete_tree``s its directory, so a write
    still in flight at that moment could land a file behind the cleanup and leave an
    orphan the next upload would trip over. Re-raising in input order keeps the
    surfaced error the same one the sequential loop would have raised.

    ``return_exceptions=True`` hands the failures back in place instead, for the
    callers that report a per-item outcome (which skill activated, which bot took
    the config) rather than failing the request as a whole. The bound and the drain
    are unaffected — that flag chooses only what happens *after* the batch is
    complete. It is the same name and meaning ``asyncio.gather`` gives it, and the
    reason those callers can move onto this helper at all.

    Cancellation is drained the same way, and needs its own handling because it does
    not travel the exception path: device filesystems block inside
    ``asyncio.to_thread``, which cannot interrupt a call already executing on a
    worker thread — cancelling only abandons the await while the HTTP write keeps
    going. Worse, ``CancelledError`` is a ``BaseException``, so
    an ``except Exception`` compensation (``LocalSkillUploadService``'s, say) is
    skipped while its ``finally`` still releases the edit lease. A retry could then
    acquire the lease, ``delete_tree`` the directory and start a fresh package that
    the abandoned writes land into. So the batch is shielded and drained before the
    cancellation is allowed to continue.
    """
    semaphore = asyncio.Semaphore(DEVICE_IO_CONCURRENCY)
    # Set when the caller is cancelled, to stop the batch admitting anything new.
    # Only calls already running need draining; see the ``except`` below.
    aborted = False

    async def _bounded(coro):
        try:
            async with semaphore:
                if aborted:
                    # Queued behind the bound when the cancellation landed, so this
                    # call never reached the device and has nothing to leave behind.
                    # Returning here is what keeps the unwind bounded by the slowest
                    # call in flight rather than by every remaining wave.
                    return None
                return await coro
        finally:
            # A coroutine that never ran — this one, or one still queued when the
            # batch was torn down — would otherwise surface as a RuntimeWarning.
            # Closing an already-finished coroutine is a no-op, so this is safe on
            # the normal path too.
            coro.close()

    batch = asyncio.ensure_future(
        asyncio.gather(*(_bounded(coro) for coro in coroutines), return_exceptions=True)
    )
    try:
        results = await asyncio.shield(batch)
    except BaseException:
        # Shielding keeps ``batch`` running when the caller is cancelled; awaiting it
        # here is what guarantees no call is still in flight once this raises.
        #
        # Draining is only owed to calls that have already started: ``to_thread``
        # cannot interrupt one that is executing on a worker thread, so abandoning
        # it would let the write land after the caller's compensation. A call still
        # queued on the semaphore has touched nothing, so it is simply dropped —
        # ``aborted`` makes each remaining entry take the fast path above instead of
        # issuing its device call. Without that, cancelling a 100-file upload ran the
        # other 92 files to completion first, which is neither needed for safety nor
        # what a caller asking to stop expects.
        #
        # Assignment before the drain is what makes this ordering hold: nothing
        # between here and the first ``await`` yields, so no queued entry can slip
        # past the flag.
        aborted = True
        # The drain is itself shielded, and loops, because cancellation can arrive
        # more than once — an aborted request overlapping with shutdown, say. A
        # plain ``await`` here would let that second cancel tear down ``batch``
        # itself and re-raise with the worker-thread writes still running, which is
        # exactly the failure the shield above prevents on the first cancel. Only
        # ``CancelledError`` is absorbed, and only until ``batch`` finishes, so this
        # delays cancellation by at most the work already in flight.
        while not batch.done():
            try:
                await asyncio.shield(batch)
            except asyncio.CancelledError:
                continue
        raise
    if not return_exceptions:
        for result in results:
            if isinstance(result, BaseException):
                raise result
    return results


__all__ = ["DEVICE_IO_CONCURRENCY", "gather_device_io"]
