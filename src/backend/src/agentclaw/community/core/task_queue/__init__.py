"""Distributed task queue — durable, DB-backed background work.

A generic component: callers persist a unit of work (``task_type`` +
JSON payload) to the ``ac_task_queue`` table; an in-process
:class:`~agentclaw.community.core.task_queue.services.worker.TaskWorker` (one per
pod) polls the DB, **claims** due tasks with a row-level compare-and-swap
UPDATE, and runs the handler registered for the task's ``task_type``.

Single-claimer-at-a-time is enforced entirely at the database level by the
claim CAS predicate — no ``SELECT ... FOR UPDATE``. A crashed worker's task
is reclaimed once its lease expires. Give-up is a wall-clock deadline
measured from first enqueue (not an attempt cap).
"""
