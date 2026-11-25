# Engine file upload errors

`POST /api/file/upload` accepts a multipart `file` and `target_path`. When the
filesystem operation reports `EROFS`, the Engine responds with HTTP 403 and
`{"detail":{"code":"filesystem_read_only"}}`. The marker does not include the
filesystem path. Callers may use the exact status and marker to distinguish a
read-only filesystem from other failures; other 403 responses are not this
verdict. Older Engine versions do not return this marker and their failures
must not be reclassified solely by status. The operation is not guaranteed to
be atomic: a failed upload may have created parent directories before failing.
