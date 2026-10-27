# BBS V4 Topic Types and Attachments

Date: 2026-10-27

## Scope

The BBS catalogue supports `DISCUSSION`, `SHARE`, `POLL`, and `HELP` filters while retaining the independent all/mine author scope. `NOTICE` remains readable for compatibility but is not a product publish option.

The publish editor supports:

- discussion: title and body;
- share: title, body, and optionally up to 5 uploaded files;
- poll: title, body, 2-10 options, single/multiple choice, and an optional deadline;
- help: title, body, and an optional free-text expected resolution time.

Personal-center changes are out of scope.

## Topic configuration contract

Topic configuration remains a JSON extension selected by `topic_type`.

```json
{
  "topic_type": "SHARE",
  "topic_config": {
    "share": { "attachment_ids": [] }
  }
}
```

```json
{
  "topic_type": "HELP",
  "topic_config": {
    "help": { "expected_time": "希望本周内" }
  }
}
```

Exactly one matching section is allowed. `POLL` requires `poll`, `SHARE` requires
`share`, `HELP` accepts an optional `help`, and `DISCUSSION`/`NOTICE` reject
configuration. `share.attachment_ids` may be empty and accepts at most 5 IDs.

## Attachment contract

Attachments are optional for a SHARE Topic. When files are selected, every file must
finish uploading successfully before the Topic is created; an empty
`attachment_ids` list is valid.

- `POST /openapi/v1/collaboration/tasks/bbs/attachments`
- `GET /openapi/v1/collaboration/tasks/bbs/attachments/{attachment_id}/content`
- Internal `/api/v1/collaboration/tasks/bbs/...` mirrors both operations.

Upload is multipart with `author_type`, `author_id`, and `file`. Files must be non-empty and at most 20 MiB. The response returns opaque attachment metadata; storage keys are never exposed.

An attachment row is tenant/environment scoped and starts unbound. SHARE creation locks every referenced row, verifies owner and unbound state, creates the Topic, and binds the rows in the same database transaction. The Topic JSON stores only attachment IDs. Idempotent Topic replay does not rebind attachments.

The object is written before metadata. If metadata persistence fails, the service attempts object deletion and propagates the persistence error. Object-store failure never returns success.

## Compatibility

Existing DISCUSSION, POLL, and NOTICE rows remain readable. The poll option maximum becomes 10 for new writes. Existing persisted polls with more options remain readable; no data migration rewrites prior rows.
