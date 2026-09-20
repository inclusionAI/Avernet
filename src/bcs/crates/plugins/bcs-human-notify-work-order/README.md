# Work-order human mention notices

## Context boundary

This Plugin implements `bcs-human-notify-api::HumanMentionNotifier` and posts one
Backend `HUMAN_GROUP_MENTIONED` NOTICE per mentioned human. It consumes only the
Plugin API notification schema and its configured Backend URL/signing key; it
must not query Group or Session services or stores.

## Notice text

The title remains `你被 @ 了`. `content.text` identifies the originating Group and,
when `session_id` is nonempty, the Session before the existing sender/message
summary:

```text
群：研发协作群（ID：group-1）
会话：发布问题排查（ID：group-1:s1）
张三: 请帮忙确认一下
```

Absent or whitespace-only names display `未命名群` / `未命名会话`; IDs always
remain intact. Group-level messages omit the Session line even if a title is
provided. Only the sender/message summary is truncated to 200 Unicode characters
plus an ellipsis; context lines do not consume this budget.

`biz_id`, `biz_data`, recipients, event constants, principal signing, timeouts and
partial-success semantics are unchanged. There is no Backend schema/config
migration. Rollback requires deploying matching Service API, Plugin API, producer
and provider builds together.

## Validation

- `cargo test -p bcs-human-notify-work-order`
- `cargo test -p bcs-human-notify-dummy` (shared Plugin API conformance)
- `cargo test -p bcs-message-flow --test contract_human_notify_context`
- `cargo test -p bcs --lib human_notify`

Payload tests cover named/unnamed contexts, group-only messages, UTF-8 truncation
and the actual HTTP request. Message-flow tests cover WebSend, group-level sends,
Bot replies, missing sessions, read failures and cross-Group title isolation.
