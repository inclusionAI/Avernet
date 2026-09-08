"""FastAPI HTTP adapter over the agent compute ``run()`` API.

Endpoints:
- ``POST /run``          — sync run, full result in the response
- ``POST /jobs``         — submit an async run, returns ``job_id``
- ``GET  /jobs/{id}``    — poll job status + per-node statuses
- ``GET  /jobs/{id}/stream`` — Server-Sent Events of per-node progress
- ``GET  /jobs/{id}/report`` — the full task report as HTML
- ``GET  /health``       — liveness probe
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from agentcompute.community.bootstrap import Config, get_config, get_container, set_config

from ..._config import load_dotenv
from ...bootstrap._yaml_config import ApplicationConfig, load_config
from ...core import NodeStatus, PlanError, RunRepository, render_report
from ...spi._agent import AgentSpec
from ...spi._logger import LoggerPlugin
from ...spi._tracer import TracerPlugin
from ._jobs import Job, JobStore, job_to_dict

__all__ = ["create_app"]


class AgentSpecModel(BaseModel):
    name: str
    role: str
    instructions: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    goal: str
    agents: list[AgentSpecModel] = Field(min_length=1)
    provider: str | None = None
    driver: str | None = None
    replanner: str | None = None
    driver_options: dict[str, Any] = Field(default_factory=dict)
    replanner_options: dict[str, Any] = Field(default_factory=dict)
    max_workers: int = 8
    # NOTE: `env_file` is deliberately NOT exposed on the HTTP API — accepting
    # an attacker-supplied path here would allow arbitrary file reads and env
    # var injection (e.g. redirecting `OPENAI_*` to a hostile endpoint). Env
    # loading is server-controlled only, via `application.yaml` / `.env`.
    llm_options: dict[str, Any] = Field(default_factory=dict)


def create_app(
    yaml_path: str | Path = "config/application.yaml",
    store: JobStore | None = None,
) -> FastAPI:
    app_config: ApplicationConfig = load_config(yaml_path)
    store = store or JobStore()
    load_dotenv(app_config.get("env_file", ".env"))
    set_config(_config_from(app_config))

    repository = _repository()
    repository.init_schema()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        repository.db.close()

    app = FastAPI(title=app_config.get("app.name", "agentcompute"), lifespan=lifespan)
    app.state.store = store
    app.state.repository = repository

    tracer = _tracer(app_config)
    tracer.setup(app_config.get("app.name", "agentcompute"))
    tracer.install_middleware(app)

    logger = _logger(app_config)
    logger.configure(
        log_level=app_config.get("log.level", "INFO"),
        log_dir=app_config.get("log.log_dir", ""),
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/run")
    def run_sync(req: RunRequest) -> dict[str, Any]:
        return _execute(req, store, repository=repository)

    @app.post("/jobs")
    def submit(req: RunRequest) -> dict[str, str]:
        job = store.create(req.goal)
        job.status = "running"
        _execute(req, store, job, repository)
        return {"job_id": job.id}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job_to_dict(job)

    @app.get("/jobs/{job_id}/stream")
    async def stream_job(job_id: str) -> StreamingResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        async def event_stream() -> AsyncIterator[str]:
            if job.status in ("completed", "failed"):
                yield _sse({"event": job.status, "data": job_to_dict(job)})
                return
            queue = store.subscribe(job_id)
            try:
                yield _sse({"event": "status", "data": {"status": job.status}})
                while True:
                    event = await queue.get()
                    yield _sse(event)
                    if event.get("event") in ("completed", "failed"):
                        break
            finally:
                store.unsubscribe(job_id, queue)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/jobs/{job_id}/report", response_class=HTMLResponse)
    def job_report(job_id: str) -> str:
        job = store.get(job_id)
        if job is None or job.full_plan is None:
            raise HTTPException(status_code=404, detail="job or plan not found")
        return render_report(
            job.full_plan,
            goal=job.goal,
            final_output=job.final_output,
            succeeded=job.status == "completed",
        )

    return app


def _config_from(app_config: ApplicationConfig) -> Config:
    plugins = app_config.get("user_config.plugins", {}) or {}
    return Config(
        llm_provider=app_config.get("user_config.plugins.llm.provider", "openai"),
        logger=plugins.get("logger", "stdlib"),
        tracer=plugins.get("tracer", "stdlib"),
        database=plugins.get("database", "sqlite"),
        options={
            "llm": app_config.get("user_config.plugins.llm", {}) or {},
            "database": app_config.get("user_config.database", {}) or {},
            "avernet": app_config.get("user_config.plugins.avernet", {}) or {},
        },
    )


def _logger(app_config: ApplicationConfig) -> LoggerPlugin:
    from agentcompute.community.bootstrap import get_container

    return get_container().plugins().logger()


def _repository() -> RunRepository:
    from agentcompute.community.bootstrap import get_container

    from ...core import RunRepository

    return RunRepository(db=get_container().plugins().database())


def _tracer(app_config: ApplicationConfig) -> TracerPlugin:
    from agentcompute.community.bootstrap import get_container

    return get_container().plugins().tracer()


def _execute(
    req: RunRequest,
    store: JobStore,
    job: Job | None = None,
    repository: RunRepository | None = None,
) -> dict[str, Any]:
    specs = [
        AgentSpec(name=a.name, role=a.role, instructions=a.instructions, metadata=a.metadata)
        for a in req.agents
    ]
    from agentcompute.community.plugins import register_plugins

    from ...plugins import register_agents

    register_plugins()
    register_agents(specs)

    base_config = get_config()
    driver = req.driver or base_config.driver
    replanner = req.replanner
    if replanner is None and driver == "dynamic":
        replanner = "dynamic"
    options: dict[str, Any] = {
        "llm": req.llm_options,
        "database": base_config.options.get("database", {}),
        "avernet": base_config.options.get("avernet", {}),
    }
    if req.driver_options:
        options["driver"] = req.driver_options
    if req.replanner_options:
        options["replanner"] = req.replanner_options
    set_config(
        Config(
            llm_provider=req.provider or base_config.llm_provider,
            logger=base_config.logger,
            tracer=base_config.tracer,
            database=base_config.database,
            planner=base_config.planner,
            driver=driver,
            replanner=replanner,
            options=options,
        )
    )

    provider = req.provider or base_config.llm_provider
    # Strip `llm_options` from the persisted payload: callers may include
    # `api_key`/`secret` here, and `save_request` writes the full dict to
    # SQLite as plaintext JSON. Credentials must never be persisted.
    persisted = req.model_dump(exclude={"provider", "llm_options"})
    persisted["provider"] = provider
    request_id = repository.save_request(persisted) if repository is not None else None
    run_id = None
    if repository is not None and request_id is not None:
        run_id = repository.start(request_id, req.goal, [a.name for a in specs], provider)

    from ..._logger import set_job_id

    set_job_id(job.id if job is not None else "-")

    def _fail_plan(error: str) -> dict[str, Any]:
        if repository is not None and run_id is not None:
            repository.fail_planning(run_id, error)
        if job is not None:
            job.status = "failed"
            job.error = error
            job.publish({"event": "failed", "data": {"error": error}})
        return {
            "goal": req.goal,
            "succeeded": False,
            "status": "failed_planning",
            "error": error,
            "node_statuses": {},
            "final_output": None,
            "plan": None,
        }

    try:
        plan = get_container().plugins().planner().plan(req.goal, specs)
    except PlanError as exc:
        return _fail_plan(str(exc))

    if repository is not None and run_id is not None:
        repository.mark_running(run_id)
        repository.save_plan(run_id, plan)
    if job is not None:
        job.plan = plan.to_dict()
        job.full_plan = plan

    def on_progress(node_id: str, status: NodeStatus, progress: float) -> None:
        if job is not None:
            job.node_statuses[node_id] = status.value
            job.publish(
                {
                    "event": "progress",
                    "data": {"node_id": node_id, "status": status.value, "progress": progress},
                }
            )
        if repository is not None and run_id is not None:
            repository.touch_node(
                run_id,
                node_id,
                plan.nodes[node_id].agent,
                status.value,
                progress,
            )

    result = get_container().plugins().driver().run(
        plan, on_progress=on_progress, max_workers=req.max_workers
    )

    for node_id, node in plan.nodes.items():
        if repository is not None and run_id is not None:
            repository.finish_node(run_id, node_id, node.status, node.result, node.error)

    if job is not None:
        job.plan = plan.to_dict()

    summary = {
        "goal": req.goal,
        "succeeded": result.succeeded,
        "status": "completed" if result.succeeded else "failed",
        "node_statuses": {nid: s.value for nid, s in result.log.statuses.items()},
        "final_output": result.final_output,
        "plan": plan.to_dict(),
    }
    if repository is not None and run_id is not None:
        repository.finish(run_id, summary["status"])

    if job is not None:
        job.status = summary["status"]
        job.final_output = result.final_output
        job.publish({"event": job.status, "data": summary})
    return summary


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
