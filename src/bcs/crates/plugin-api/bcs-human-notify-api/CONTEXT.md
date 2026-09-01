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
