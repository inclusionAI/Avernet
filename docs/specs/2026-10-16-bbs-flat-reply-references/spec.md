# BBS reply references and two-level floor groups

## Decision

BBS stores every Post as one chronological row with at most one nullable
`reply_to_post_id`. It never persists floor numbers, floor tables, recursive
hierarchy, or materialized child-post collections. Product reads expose a two-level
presentation view: direct Topic replies are first-level floors, and every
descendant reply belonging to the same first-level floor is returned as a child
of that floor.

## Storage

`ac_forum_post.reply_to_post_id` is a nullable `varchar(128)` reference. The
column uses the same byte-sensitive collation as other BBS ids. The migration
adds no index or recursive-hierarchy fields because all reads are still
Topic-scoped. Existing and direct replies keep `NULL`.

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
before status and reference validation.

## Read semantics

`GET /topics/{topic_id}/posts` pages **first-level floors only**: direct Topic
replies whose `reply_to_post_id` is null. `total`, `page`, and `page_size`
therefore count floors, not every Post.

Each visible first-level floor carries all descendants that follow it:

```json
{
  "post_id": "post_456",
  "topic_id": "topic_789",
  "body": "reply body",
  "reply_to_post_id": null,
  "reply_to_floor_no": null,
  "children": [
    {
      "post_id": "post_457",
      "reply_to_post_id": "post_456",
      "reply_to_floor_no": 1,
      "body": "floor-group reply"
    }
  ]
}
```

- `children` is read-only and appears only on first-level list items.
- A reply to another child remains a sibling in the same parent's `children`.
- Child entries never carry another `children` array.
- `reply_to_floor_no` is provided when the addressed Post is a first-level
  floor; a target child has no floor number.
- Child replies always follow their visible parent and have no independent
  pagination token.

## Frontend behavior

The detail page renders:

```text
1楼 Alice
  回复 @Alice
  回复 @Bob
2楼 Bob
```

- First-level floors receive sequential floor numbers.
- Child replies do not display floor numbers.
- More than two child replies show only the first two until the user expands
  the group.
- More than two child replies show the first two by default with
  `查看全部 N 条回复 ▾`; expanded groups show `收起 ▴`.
- Pagination changes reset expansion state.
- The target author is highlighted with the primary token and clicking it
  scrolls to the addressed reply, including a child target.

## Compatibility

- Legacy replies with `NULL` remain first-level floors.
- An old client that ignores `children` still receives the direct floor page.
- The topic catalogue's `reply_count` and `latest_activity_at` already count
  all Posts, including every child reply.
- No storage migration is required.
