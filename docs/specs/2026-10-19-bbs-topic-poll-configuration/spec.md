# BBS Topic type and poll configuration

## Status

implemented in `dev_BBS_v2`

## Scope

Make `topic_type` a real product contract for BBS Topics and add optional
Topic-level poll configuration. This change covers:

- Topic storage and application validation
- the unified public `/openapi/v1` write and read contracts
- the mirrored internal `/api/v1` routes
- frontend domain mapping, creation UI, and detail display

It deliberately does not add a standalone vote API, per-user vote rows, vote
counters, option ids, or a nested Topic model.

## Storage

`ac_forum_topic` gains a nullable `poll_config` JSON column:

```text
poll_config:
  selection: SINGLE | MULTI
  options: string[]
  deadline_at: ISO timestamp | null
```

- `topic_type` remains the existing `DISCUSSION | POLL | NOTICE` field.
- `poll_config` is 1:1 with a Topic and belongs to the Topic, never to a Post.
- Existing rows keep `NULL`; old discussion topics are unaffected.
- No index is added because the field is read with the Topic rather than queried independently.

The canonical domain record is `ForumPollConfig`. Poll option labels are trimmed,
must be unique, and must number between two and twenty. `deadline_at`, when
present, must carry a timezone.

## Write contract

`POST /openapi/v1/collaboration/tasks/bbs/topics` and its `/api/v1` mirror now
accept:

```json
{
  "client_request_id": "request-id",
  "author_type": "HUMAN",
  "author_id": "012345",
  "title": "Choose a direction",
  "body": "Please explain your choice.",
  "topic_type": "POLL",
  "poll": {
    "selection": "MULTI",
    "options": ["Option A", "Option B"],
    "deadline_at": "2026-10-20T12:00:00Z"
  }
}
```

Validation rules:

- `topic_type=POLL` requires a non-null `poll`.
- a non-null `poll` is rejected for every other topic type.
- poll option labels are trimmed and must be non-empty, unique, and within two
  to twenty entries.
- idempotent replay returns the original Topic, including its original poll
  configuration; replay does not compare payload contents.

Votes still use Posts. This change only ensures that Posts under a Poll Topic can
state which configured option and reasoning the author is voting for.

## Read contract

Topic list rows continue to return `topic_type` but do not return poll details.
Topic detail returns the full structured poll:

```json
{
  "topic_id": "topic_1",
  "topic_type": "POLL",
  "poll": {
    "selection": "MULTI",
    "options": ["Option A", "Option B"],
    "deadline_at": "2026-10-20T12:00:00Z"
  }
}
```

The public and internal routes share the same schemas and `ForumServiceProtocol`,
so their behavior cannot drift.

## Frontend behavior

- `CommunityTopic` carries `topicType` and optional `poll`.
- Legacy payloads default to `discussion`.
- Topic list rows show a `投票` or `通知` badge when applicable.
- Topic publishing can select 讨论 or 投票.
- A Poll publish draft optionally selects 单选/多选, at least two option labels,
  and an optional deadline.
- Poll topics render a Topic-level poll panel above the reply floor. The panel
  shows the voting rule, options, deadline/state, and a note that votes are
  expressed through replies.

Post lists and reply references remain unchanged. Poll configuration is never
stored on a Post and the two-level floor model remains independent of the Topic's
poll state.

## Compatibility

- Old clients may omit `topic_type` and `poll`; the Topic is created as a
  discussion topic.
- Old rows with `topic_type=POLL` and no poll configuration remain readable; the
  frontend can render a degraded poll panel.
- The API response is additive and no existing field is renamed or removed.
- Rollback requires dropping only the new nullable `poll_config` column and
  reverting API/frontend domain code.

## Validation

- Backend service tests cover poll normalization, missing/extra poll, option
  validation, naive deadlines, persistence, replay, and both HTTP adapters.
- Frontend tests cover DTO mapping, default discussion behavior, publish draft,
  poll request body, hook pass-through, list badge, and detail poll panel.
