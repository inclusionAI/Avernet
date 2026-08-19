"""Plugin implementations for the community (open-source) edition.

Each sub-package supplies the community implementation registered via the
``gateway.*`` entry-point groups in ``pyproject.toml``:

- ``runner.bare``   — ``BareAppRunnerPlugin`` (uvicorn)
- ``logger.bare``   — ``BareLoggerPlugin``   (stdlib logging)
- ``tracer.bare``   — ``BareTracerPlugin``   (OpenTelemetry)
- ``cache.in_memory`` — ``InMemoryCachePlugin``  (in-memory dict)
- ``cache.redis``   — ``RedisCachePlugin``  (Redis, server-side TTL)
- ``auth.stub``     — ``StubAuthPlugin``     (hardcoded user stub)
- ``database.sqlite`` — ``SqliteDatabasePlugin`` (SQLite in-memory)
- ``secret_resolver.community`` — ``CommunitySecretResolver`` (env-backed)
- ``secret_resolver.kms`` — ``AliyunKmsSecretResolver`` (Aliyun KMS)

Enterprise registers ``sofa`` counterparts under the same groups.
"""
