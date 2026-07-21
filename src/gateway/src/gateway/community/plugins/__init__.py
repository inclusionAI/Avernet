"""Plugin implementations for the community (open-source) edition.

Each sub-package supplies the ``bare`` implementation registered via the
``gateway.*`` entry-point groups in ``pyproject.toml``:

- ``runner.bare``   — ``BareAppRunnerPlugin`` (uvicorn)
- ``logger.bare``   — ``BareLoggerPlugin``   (stdlib logging)
- ``tracer.bare``   — ``BareTracerPlugin``   (OpenTelemetry)
- ``cache.bare``    — ``BareCachePlugin``    (in-memory dict)
- ``auth.bare``     — ``BareAuthPlugin``     (hardcoded user stub)
- ``database.bare`` — ``BareDatabasePlugin`` (SQLite in-memory)

Enterprise registers ``sofa`` counterparts under the same groups.
"""
