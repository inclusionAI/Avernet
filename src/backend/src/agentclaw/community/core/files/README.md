# core/files

Teclaw bot **workspace-file metadata** (`ac_file`).

Teclaw is a pull-based engine: it receives the `BotConfigArtifact` (a manifest of
the files it should have), fetches them, and manages its own copy. So when the
file manager uploads/deletes a file for a teclaw bot, the backend records a row
here; compose reads these rows and emits `{store, path}` refs into the artifact so
the container fetches/drops the file. Teclaw-only — arca/local write the live FS
and record nothing here.

- `models.py` — `FileRecord` (one `ac_file` row).
- `repository/protocol.py` — `FileRepositoryProtocol` (create / get_by_path /
  list_by_path_prefix / list_by_bot / delete). Single impl at
  `plugins/file_repository.py` (DB injected; no local/prod split).
- `service.py` — `BotFileService` (record-before-deliver upload, delete-by-path,
  mkdir).
- The ORM model (`plugin_api/models.py:FileModel`) is the schema source of truth.
  Local/tests auto-create it via `Base.metadata.create_all`; prod DDL is applied
  to OceanBase out-of-band.

## Context Boundary

```yaml
purpose: "Teclaw bot workspace-file metadata (ac_file) — records uploaded files so compose references them in the artifact."
provides:
  - "FileRecord model"
  - "FileRepository protocol"
consumes:
  - "DatabasePlugin"
  - "DeviceFileSystem (for the byte write/delete, via the service)"
internal_dependencies:
  - agentclaw.community.core.base
  - agentclaw.community.log
  - agentclaw.community.plugin_api.database
  - agentclaw.community.plugin_api.models
```
