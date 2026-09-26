# Legacy Skill upload error contract

## Scope

This contract covers the legacy `POST /api/skills/upload` endpoint. It is a
backward-compatible envelope change: existing clients can continue reading
`success`, `data`, and `message`, while new clients use `error_code` for stable
branching and localized presentation.

The endpoint still returns HTTP 200 for business validation failures. HTTP 4xx,
409, 422, and 503 responses produced by authentication, request parsing, or the
edit-lock interceptor remain governed by their existing contracts.

## Response shape

```json
{
  "success": false,
  "data": null,
  "message": "SKILL.md is required.",
  "error_code": "SKILL_MANIFEST_MISSING"
}
```

`error_code` is `null` on a successful upload. Existing `message` text remains
for compatibility and diagnostics; clients must not use it as the programmatic
contract.

## Stable error codes

| `error_code` | Meaning | Current message source |
| --- | --- | --- |
| `SKILL_BOT_NOT_FOUND` | Target Bot does not exist for the owner | `Bot not found.` |
| `SKILL_BOT_OWNER_MISSING` | Bot ownership metadata is incomplete | `Bot ownership metadata is incomplete.` |
| `SKILL_BOT_NOT_READY` | Bot is not `ACTIVE` | `Bot is not ready...` |
| `SKILL_FILE_PATHS_INVALID` | `file_paths` is not a JSON array | `file_paths must be a JSON array.` |
| `SKILL_FILE_PATHS_COUNT_MISMATCH` | Number of paths differs from files | `file_paths length must match files length.` |
| `SKILL_NO_FILES` | No usable files remain after filtering | `No files uploaded` |
| `SKILL_MANIFEST_MISSING` | `SKILL.md` is missing | `SKILL.md is required.` |
| `SKILL_MANIFEST_MULTIPLE` | More than one `SKILL.md` was supplied | `Only one skill can be uploaded...` |
| `SKILL_MANIFEST_INVALID` | Manifest/frontmatter cannot be parsed or is incomplete | Parser validation message |
| `SKILL_FILE_OUTSIDE_ROOT` | A submitted file is outside the detected Skill root | `Upload contains files outside the skill root directory.` |
| `SKILL_MANIFEST_ENCODING_INVALID` | `SKILL.md` is neither UTF-8 nor GBK | `SKILL.md must be encoded...` |
| `SKILL_MANIFEST_FIELD_TYPE_INVALID` | `name` or `description` is not a string | `SKILL.md field ... must be a string.` |
| `SKILL_NAME_MISSING` | Manifest has no `name` field | `SKILL.md must contain required field: name.` |
| `SKILL_NAME_EMPTY` | Manifest `name` is empty | `SKILL.md field 'name' cannot be empty.` |
| `SKILL_NAME_TOO_LONG` | Manifest `name` exceeds the parser limit | `SKILL.md field 'name' cannot exceed...` |
| `SKILL_DESCRIPTION_MISSING` | Manifest has no `description` field | `SKILL.md must contain required field: description.` |
| `SKILL_DESCRIPTION_EMPTY` | Manifest `description` is empty | `SKILL.md field 'description' cannot be empty.` |
| `SKILL_DESCRIPTION_TOO_LONG` | Manifest `description` exceeds the parser limit | `SKILL.md field 'description' cannot exceed...` |
| `SKILL_ROOT_NAME_MISMATCH` | Skill folder and manifest names differ | `Skill folder name must match...` |
| `SKILL_PATH_INVALID` | Upload path is unsafe | `Invalid upload path: ...` |
| `SKILL_NAME_INVALID` | Skill name violates the naming rule | `Skill name ... is invalid...` or underscore validation |
| `SKILL_NAME_RESERVED` | Skill name is reserved | `Skill name ... is reserved...` |
| `SKILL_ZIP_INVALID` | ZIP payload cannot be read | ZIP parser error |
| `SKILL_PACKAGE_TOO_LARGE` | Package exceeds a governed size or file-count limit | `Skill package exceeds the allowed size limits.` |
| `SKILL_RUNTIME_UNAVAILABLE` | Bot runtime/device is unavailable or timed out | Normalized runtime message |
| `SKILL_UPLOAD_FAILED` | Unexpected upload/storage failure | `Upload failed: ...` |

The list is additive. A new validation category must receive a new stable code;
reusing a code for a different meaning is a breaking change.

## Frontend guidance

For `success=false`, map `error_code` to a friendly localized message and use
`message` only as a fallback for an unknown code. Do not show raw exception
text as the primary user-facing copy. HTTP error responses should continue
through the shared request error handler.

## Package limits

The legacy endpoint now enters the same validated complete-package lifecycle as
the folder and OpenAPI upload entry points. Before any device write it enforces:

- compressed ZIP: 10 MiB;
- expanded total: 50 MiB;
- one file: 10 MiB;
- file count: 500;
- normalized relative path length: 256 characters (reported as
  `SKILL_PATH_INVALID`).

Size and file-count failures return `SKILL_PACKAGE_TOO_LARGE`. Existing clients
still receive the legacy HTTP-200 failure envelope; clients that branch on
stable codes can distinguish these limits from malformed ZIP, invalid paths,
or runtime failure.
