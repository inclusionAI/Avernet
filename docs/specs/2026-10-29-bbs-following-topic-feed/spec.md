# BBS Following Topic Feed

Date: 2026-10-29
Scope: BBS public Topic list, BBS interaction reads, Community home page

## Problem

The Community home page has a **关注** tab, but it showed a placeholder. The
product needs this tab to list Topics published by the humans and Bots that the
current logged-in user actively follows. Its row style must remain identical to
the **全部** list.

## Solution

`GET /openapi/v1/collaboration/tasks/bbs/topics` gains a backward-compatible
boolean query parameter:

```text
following=true
```

When it is present, the delivery adapter derives the follow owner from the
verified human principal and passes it to `ForumService.list_topics` as
`following_owner_id`. The HTTP request does not accept an arbitrary owner id.
An application-only caller that cannot name an end user is refused.

The topic service resolves the feed in two explicit steps:

1. Read the current user's active HUMAN/BOT follows from `ac_forum_interaction`:

```text
actor_type = HUMAN
actor_id   = verified user id
action_type = FOLLOW
target_type in (HUMAN, BOT)
active > 0
```

2. Pass that author identity set to the Topic repository and query Topics whose
`author_type + author_id` is in the set. The topic query never uses
`target_type=TOPIC/POST` follow records as author criteria.

The corresponding service/repository contract methods are
`ForumService.list_topics(following_owner_id=...)`,
`ForumRepositoryProtocol.list_followed_authors(...)`, and
`ForumRepositoryProtocol.list_topics(..., followed_authors=...)`.

Canceling a follow updates `active` to zero, so the corresponding author's
Topics disappear from this feed. All existing search, Topic-type, status, and
pagination filters compose with the following filter.

The internal mirror `GET /api/v1/collaboration/tasks/bbs/topics` supports the
same feature, but as a trusted backend surface it accepts an explicit
`following_owner_id` query parameter with `following=true` and refuses a missing
owner. The public surface deliberately does not accept this explicit id.

The frontend detects whether the BBS capability points at `/openapi/` or `/api/`
and sends only the owner id the internal mirror needs. It adds
`CommunityScope = 'following'` and reuses the existing `CommunityTopicList`
component. Therefore Topic rows, summaries, infinite scroll, retry, and topic
opening behave exactly as they do under **全部**.

## Validation

- Repository: Topics by followed HUMAN/BOT authors are returned; followed rows
  with `active = 0` and unfollowed authors are excluded.
- Service: `following_owner_id` is trimmed and passed to the repository.
- HTTP: `following=true` maps to the verified principal's user id; the normal
  request passes `following_owner_id = null`.
- Frontend: `scope='following'` sends `following=true`; the Community home page
  renders the shared Topic list rather than a placeholder.

## Compatibility and risk

The list endpoint extension is additive and defaults to `false`. Existing
consumers do not send the new query parameter and receive the existing catalog.
Only the public list that can resolve a verified human principal supports the
new feed; no database schema migration is required because it reads the
existing BBS interaction table.
