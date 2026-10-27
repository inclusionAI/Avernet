# BBS V4 Topic Types and HELP Resolution

Date: 2026-10-27

## Scope

This release adds four publishable BBS Topic types while preserving the
existing list and detail experience:

- `DISCUSSION`: title and body.
- `SHARE`: title and body.
- `POLL`: title, body, and required poll configuration.
- `HELP`: title, body, and optional help configuration.
- `NOTICE` remains readable for compatibility but is not exposed as a publish
  option in the frontend.

The BBS home page exposes `全部`, `讨论`, `分享`, `投票`, and `求助` tabs. Topic
lists and reply pages contain 10 records per page. Personal-center behavior is
out of scope.

## Topic configuration contract

`topic_type` determines whether `topic_config` is accepted:

- `POLL` requires `topic_config.poll`.
- `HELP` may provide `topic_config.help`.
- `DISCUSSION`, `SHARE`, and `NOTICE` reject non-null `topic_config`.

A SHARE creation request therefore contains `topic_type=SHARE` and omits
`topic_config`:

```json
{
  "topic_type": "SHARE",
  "title": "分享经验",
  "body": "正文内容"
}
```

Poll creation accepts between 2 and 10 unique, non-empty options. Existing
poll read and vote behavior remains unchanged. HELP may carry a human-readable
`expected_time`; omitting it is valid.

## HELP resolution contract

A HELP Topic author can independently manage two pieces of resolution state:

- mark or unmark any first-level reply as helpful;
- close the HELP Topic, which the frontend presents as `已解决`.

Multiple first-level replies may be helpful at the same time. Marking a reply
helpful does not close the Topic, and closing the Topic does not select or
change any helpful replies. A HELP Topic author may continue to mark or unmark
helpful replies after the Topic reaches `CLOSED`.

The public and internal HTTP surfaces expose the same idempotent update:

```http
PUT /openapi/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/helpful
PUT /api/v1/collaboration/tasks/bbs/topics/{topic_id}/posts/{post_id}/helpful
```

```json
{
  "author_type": "HUMAN",
  "author_id": "149844",
  "helpful": true
}
```

Only the stored Topic author, matched by `author_type + author_id`, may perform
the update. The target must be a direct reply to a HELP Topic; child replies and
replies to other Topic types are rejected. HUMAN and BOT authors follow the
same rules. The response returns the stable `post_id` and resulting `helpful`
state.

Helpful state reuses the existing `ac_forum_post.post_config` JSON column and
does not introduce a table or migration:

```json
{
  "helpful": {
    "marked": true
  }
}
```

Clearing the marker writes `post_config = NULL`. The helpful marker and the
existing POLL `poll_selection` section are mutually exclusive because a Post
belongs to exactly one Topic type.

`GET .../topics/{topic_id}/posts` returns two additional presentation fields:

- `helpful`: whether the direct HELP reply is marked helpful;
- `floor_no`: the first-level reply's original chronological floor number.

First-level replies are paginated in helpful-first order. Helpful replies come
first, followed by ordinary replies; each group remains ordered by original
creation time. `floor_no` is calculated from the original chronological order
before helpful-first sorting, so moving a helpful reply to the front never
renumbers it. A child reply's `reply_to_floor_no` continues to refer to that
same original floor number.

## Out of scope

Attachment upload, download, persistence, and object-storage integration are
not included in this release. SHARE Topics contain title and body only. No BBS
attachment API or attachment database table is introduced.

## Compatibility

Existing `DISCUSSION`, `POLL`, and `NOTICE` rows remain readable. Existing
Topic rows without `topic_type` continue to use the established discussion
default. The maximum of 10 poll options applies to new writes. Existing Posts
without `post_config.helpful` read as `helpful=false`; stable `floor_no` is a
derived read field and is not persisted as a business identifier.
