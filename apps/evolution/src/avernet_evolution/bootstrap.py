"""Service composition/lifespan and CLI. One owner process per persistent volume."""

import argparse
from contextlib import asynccontextmanager
from importlib import import_module

from fastapi import FastAPI

from .adapters.control import build_control_router, install_errors
from .adapters.jobs import PREFIX, build_job_router
from .composition import compose
from .config import Settings
from .process_lock import ProcessLock


async def load_platform(settings):
    if settings.profile == "local":
        from .local import build_local_platform

        return await build_local_platform(settings)
    module, separator, name = settings.platform_factory.partition(":")
    if not separator or not module or not name or "." in name:
        raise ValueError("platform_factory must be a trusted module:async_factory")
    return await getattr(import_module(module), name)(settings)


def create_app(settings: Settings, *, platform_factory=load_platform) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        lock = ProcessLock(settings.database)
        platform = None
        runtime = None
        try:
            # Fail before opening persistent state or constructing providers.
            access = None
            if settings.profile == "production":
                from .adapters.backend_access import BackendAccess

                access = BackendAccess(
                    key=settings.backend_principal_key_file.read_text().strip(),
                    tenant=settings.backend_tenant,
                )
            platform = await platform_factory(settings)
            runtime = await compose(settings, platform)
            app.state.runtime = runtime
            # Router construction occurs before serving, once per app lifespan.
            # Startup of an already-used app is intentionally unsupported.
            app.include_router(build_control_router(runtime, backend_access=access))
            app.include_router(build_job_router(lambda: runtime.channel, prefix=PREFIX))
            app.openapi_schema = None
            await runtime.runner.startup()
            yield
        finally:
            try:
                if runtime is not None:
                    await runtime.close()
                elif platform is not None:
                    await platform.close()
            finally:
                lock.close()

    app = FastAPI(
        title="Avernet Evolution (01–06)",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_errors(app)

    @app.get("/healthz", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    return app


def main():
    parser = argparse.ArgumentParser(description="Independent Evolution service")
    parser.add_argument("command", choices=["serve"])
    parser.parse_args()
    settings = Settings.from_env()
    import uvicorn

    # No request query strings/tokens in default access logs (worker protocol has
    # legacy query credentials). Deployments can log safe trace metadata upstream.
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
