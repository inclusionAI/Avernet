import pytest

from agentcompute.community.adapters.http import JobStore, job_to_dict


def test_job_store_create_and_get():
    store = JobStore()
    job = store.create("goal")
    assert store.get(job.id) is job
    assert store.get("missing") is None


def test_job_to_dict_shape():
    store = JobStore()
    job = store.create("goal")
    data = job_to_dict(job)
    assert data["id"] == job.id
    assert data["goal"] == "goal"
    assert data["status"] == "pending"
    assert data["node_statuses"] == {}


def test_publish_reaches_existing_subscribers():
    store = JobStore()
    job = store.create("goal")
    queue = store.subscribe(job.id)
    job.publish({"event": "progress", "data": {"node_id": "a"}})
    event = queue.get_nowait()
    assert event["event"] == "progress"


def test_publish_ignored_without_subscribers():
    store = JobStore()
    job = store.create("goal")
    job.publish({"event": "progress", "data": {"node_id": "a"}})
    assert job._subscribers == []


def test_unsubscribe_removes_queue():
    store = JobStore()
    job = store.create("goal")
    queue = store.subscribe(job.id)
    assert queue in job._subscribers
    store.unsubscribe(job.id, queue)
    assert queue not in job._subscribers


@pytest.mark.asyncio
async def test_subscribe_async_receive():
    store = JobStore()
    job = store.create("goal")
    queue = store.subscribe(job.id)
    job.publish({"event": "progress", "data": {"node_id": "a"}})
    event = await queue.get()
    assert event["event"] == "progress"
