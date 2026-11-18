-- Retained immutable objects for opt-in distributed runtime namespaces.
-- No TTL or garbage collection in phase 1. Hash keys bound index width; full
-- namespace and object identity are checked on every read for collision safety.
CREATE TABLE IF NOT EXISTS bcs_dist_immutable_objects (
    namespace_hash TEXT NOT NULL,
    object_hash TEXT NOT NULL,
    namespace_key TEXT NOT NULL,
    object_key TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    source_json TEXT NOT NULL,
    content_bytes BLOB NOT NULL,
    PRIMARY KEY (namespace_hash, object_hash)
);
