# bcs-human-notify-api

## Provides
- `HumanMentionNotifier` trait、`HumanMentionNotifierFactory` inventory 注册单元、
  `HumanNotifyError`、插件自有 schema `MentionNotification`/`MentionedHuman`。

## Consumes
- `bcs-config-api`（`HumanNotifyProviderConfig`）

## Allowed dependencies
- config-api 叶子 crate；`async-trait`、`futures`、`inventory`、`thiserror`。

## Forbidden dependencies
- 具体 plugins/* 实现 crate、services/*、service-api、bootstrap、transport/runtime crate、env/文件系统访问。
- 通知 schema 由本 crate 自有（Plugin API 与 Service API 分别演化），不 re-export
  service-api 端口类型；端口 DTO 与插件 DTO 的翻译由 bootstrap 适配层完成。

## Notification schema (0.2.0)

`MentionNotification.group_name` is the optional Group label;
`session_name` is the optional Session title. Names are display metadata, never
identifiers. `session_id` remains empty for a group-level message, in which case
`session_name` is absent. An unnamed or unavailable Session also has no title.
Providers choose their own presentation for absent or blank names; they must not
query business stores to resolve names.

This Rust source-contract change requires struct-literal producers to initialize
both new fields. Bootstrap translates them explicitly from the independently
versioned Service API port. The built-in dummy and work-order providers and
conformance fixtures evolve together; external providers must rebuild against
0.2.0. HTTP/WS protocols, provider config and notification delivery/error semantics
are unchanged.
