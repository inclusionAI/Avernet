# bcs-human-notify-api

## Provides
- `HumanMentionNotifier` trait、`HumanMentionNotifierFactory` inventory 注册单元、
  `HumanNotifyError`、`MentionNotification`/`MentionedHuman` re-export。

## Consumes
- `bcs-service-api`（`MentionNotification` 等类型归属）
- `bcs-config-api`（`HumanNotifyProviderConfig`）

## Allowed dependencies
- contracts/service-api/config-api 叶子 crate；`futures`、`inventory`、`thiserror`。

## Forbidden dependencies
- 具体 plugins/* 实现 crate、services/*、bootstrap、transport/runtime crate、env/文件系统访问。
