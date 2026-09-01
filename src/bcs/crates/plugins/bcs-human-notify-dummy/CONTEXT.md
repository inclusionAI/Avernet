# bcs-human-notify-dummy

## Provides
- `DummyHumanMentionNotifier`：日志型通知后端（`backend_name = "dummy"`）。
- `inventory::submit!` 注册的 `HumanMentionNotifierFactory`（name = "dummy"）。

## Consumes
- `bcs-human-notify-api`、`bcs-config-api`。

## Allowed dependencies
- plugin-api/config-api 叶子 crate；`async-trait`、`futures`、`inventory`、`tracing`。

## Forbidden dependencies
- services/*、bootstrap、transport/runtime crate、网络或文件系统访问。
