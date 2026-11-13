"""HTTP wakeups drive one durable job; idle services perform no task scans."""

import asyncio

from test_index_builds import world


def test_on_demand_runner_is_idle_until_triggered(tmp_path, monkeypatch):
    _, repo, service = world(tmp_path)
    service.create("queued", "model", 2)

    def forbidden():
        raise AssertionError("idle runner scanned build table")

    monkeypatch.setattr(repo, "candidates", forbidden)

    async def check():
        from search_core.core.index_build_runner import IndexBuildRunner

        runner = IndexBuildRunner(service)
        stop = asyncio.Event()
        task = asyncio.create_task(runner.run(stop))
        try:
            await asyncio.sleep(0.02)
            assert repo.get("queued").status == "pending"
            runner.trigger("queued")
            runner.trigger("queued")
            for _ in range(100):
                if repo.get("queued").status == "completed":
                    break
                await asyncio.sleep(0.01)
            assert repo.get("queued").status == "completed"
        finally:
            stop.set()
            await task

    asyncio.run(check())


def test_failed_build_retried_only_after_new_request(tmp_path):
    source, repo, service = world(tmp_path)
    source.fail = "document-2"
    service.create("retry", "model", 2)

    async def check():
        from search_core.core.index_build_runner import IndexBuildRunner

        runner = IndexBuildRunner(service)
        stop = asyncio.Event()
        task = asyncio.create_task(runner.run(stop))
        try:
            runner.trigger("retry")
            for _ in range(100):
                if repo.get("retry").status == "failed":
                    break
                await asyncio.sleep(0.01)
            assert repo.get("retry").status == "failed"
            source.fail = ""
            await asyncio.sleep(0.02)
            assert repo.get("retry").status == "failed"
            service.create("retry", "model", 2)
            runner.trigger("retry")
            for _ in range(100):
                if repo.get("retry").status == "completed":
                    break
                await asyncio.sleep(0.01)
            assert repo.get("retry").status == "completed"
            assert source.calls.count("document-1") == 1
        finally:
            stop.set()
            await task

    asyncio.run(check())
