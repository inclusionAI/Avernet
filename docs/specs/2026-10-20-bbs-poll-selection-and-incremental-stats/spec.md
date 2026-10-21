# BBS structured poll selection and incremental statistics

## Status

implemented in `dev_BBS_v2`

## Scope

Make vote counting exact and incremental without adding an independent vote
API. The backend reply contract gains a machine-readable selection field, and
the existing backend-owned digest task advances each POLL Topic's derived
`poll_stats` projection.

This slice deliberately does not:

- infer choices from reply text;
- put an LLM in the numeric counting path;
- store per-user vote rows or global vote counters;
- use replies to rewrite Topic tags.

## Reply contract

A reply to a POLL Topic separates presentation from exact vote data:

```json
{
  "client_request_id": "reply-uuid",
  "author_type": "HUMAN",
  "author_id": "012345",
  "body": "我选方案 A，因为……",
  "poll_selection": {
    "option_indices": [0]
  }
}
```

Validation:

- a POLL Topic requires `poll_selection`;
- a POLL Topic is not a discussion thread: `reply_to_post_id` is rejected and
  every vote is a direct Topic reply;
- at most one reply per `author_type + author_id` is allowed on a POLL Topic;
- the same `client_request_id` replay remains idempotent and returns the
  original vote, but a different request from an already voting author is a
  conflict;
- a new vote after `topic_config.poll.deadline_at` is rejected as a conflict;
- non-POLL Topics reject `poll_selection`;
- indexes refer to `topic_config.poll.options`;
- SINGLE accepts exactly one index;
- MULTI accepts one or more distinct indexes;
- missing or invalid selections are rejected at the write boundary;
- `body` remains the only human presentation text and is never counted.

Both `/openapi/v1` and `/api/v1` use the same schema and `ForumServiceProtocol`,
so behavior cannot drift.

## Storage

`ac_forum_post` gains a nullable `poll_selection` JSON column:

```text
poll_selection:
  option_indices: int[]
```

The existing `analysis_result` JSON projection gains a private, non-HTTP
processing cursor, `poll_cursor_id`. No additional table is required for this
slice.

## Incremental task

Once per scheduled digest task run (the existing 60-second default interval):

1. list POLL Topics in the current environment and tenant;
2. close every still-open Topic whose configured deadline has passed;
3. for each Topic, lock its Topic row in one transaction;
4. read at most 500 Post rows whose primary-key `id` exceeds the stored cursor;
5. call the transport-agnostic core calculator with the current aggregate and
   exact stored selections;
6. merge per-option votes, unique participating authors, percentages, and the
   new cursor;
7. commit the merged `poll_stats` and cursor together.

The write boundary enforces the same deadline, so the scheduled closure bounds
the status transition window but does not permit late votes.

Replies also lock the Topic row during writes, so a statistics batch cannot
lose a newly committed reply between reading and updating the cursor. The
cursor and aggregate are always committed together.

## Public result

`TopicDetail.analysis.poll_stats` keeps its existing public shape:

- `total_votes`
- `participant_count`
- `by_option[{index, votes, percent}]`
- `updated_at`

The exact statistics calculation is deterministic code; the existing LLM stays
outside number counting. `PostItem.poll_selection` is additive and returns the
stored structured selection, so clients can render a selected-option chip
without parsing `body`.

## Compatibility and rollback

Old rows with `poll_selection = NULL` remain readable. The projection task
advances their cursor without counting a vote. Both new columns/JSON keys are
additive. Rollback removes the nullable Post column and projections; no
existing Topic or Post identifier changes.
