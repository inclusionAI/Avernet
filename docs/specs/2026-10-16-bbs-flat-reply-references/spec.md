# BBS flat reply references

## Decision

BBS replies remain a flat, chronologically ordered stream. A reply may name at
most one existing Post in the addressed Topic as its referenced reply. BBS does
not persist floor numbers, add a floor table, or render/store nested replies.

## Storage

`ac_forum_post.reply_to_post_id` is a nullable `varchar(128)` reference. The
column uses the same byte-sensitive collation as other BBS ids. The migration
adds no index or recursive-hierarchy fields. Existing and direct replies keep
`NULL`.

## Write semantics

`POST /openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts` and its
internal `/api/v1` mirror accept optional `reply_to_post_id`:

```json
{
  "client_request_id": "request-id",
  "body": "reply body",
  "reply_to_post_id": "post_123"
}
```

For new replies only, the transaction validates that the referenced Post exists
in the same Topic. Missing and cross-Topic references are rejected as
`NotFound`; a closed or locked Topic is rejected without a new write.

A replay of an already-created `client_request_id` returns the original Post
before status and reference validation. A previously accepted request therefore
still succeeds after the Topic later closes.

## Read semantics

`GET /topics/{topic_id}/posts` returns the stored reference and a derived
presentation field:

```json
{
  "post_id": "post_456",
  "topic_id": "topic_789",
  "body": "reply body",
  "reply_to_post_id": "post_123",
  "reply_to_floor_no": 1
}
```

`reply_to_floor_no` is calculated from the current full Topic ordering, not the
current page's local index. It is read-only and is never persisted or used as a
stable business identifier.

## Frontend behavior

The UI adds a reply action to each flat reply. Selecting it records that reply's
Post id and removes the selection without creating a nested list. A direct reply
omits `reply_to_post_id`. The UI shows `回复 N楼` when the derived floor is
available and remains a flat article stream.
