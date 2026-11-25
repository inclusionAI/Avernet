# Database initialization

`initDatabase(configPath?, options?)` connects once per process and returns the shared database. Existing callers default to `schema: "migrate"`: migrations, self-healing repairs, SQLite triggers and the existing NoOp fallback remain unchanged.

A composition root that provisions the complete schema externally may pass `{ schema: "external" }`. This connects without calling the migration runner or accessing its ledger, including its self-healing callbacks. Connection failures reject instead of returning a NoOp database. The caller owns schema compatibility and readiness checks before serving traffic. This option does not disable other modules or their initialization.

Repeated initialization with the same strategy reuses the connection. Switching strategies requires `closeDatabase()` first and otherwise rejects; an existing connection cannot silently satisfy a different initialization contract. No runtime environment variable selects this behavior in the public database library.

The default public bootstrap continues to use migrations. Consumers must opt in explicitly; this addition does not change public startup defaults or CI commands.
