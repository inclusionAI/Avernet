-- Public DbPlugin archive; enterprise providers select their database at assembly.
CREATE TABLE IF NOT EXISTS bcs_dist_immutable_objects (
    namespace_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    object_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    namespace_key VARCHAR(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    object_key VARCHAR(1024) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    content_digest VARCHAR(71) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_json TEXT NOT NULL,
    content_bytes LONGBLOB NOT NULL,
    PRIMARY KEY (namespace_hash, object_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
