# BCS Bot webhook 实施验证

分支：`codex/bcs-bot-webhook`。基线：`origin/dev` 的
`e8cdf07b2057438c44668e437e88950d4bb2480c`。

## 已实现

- Provider 默认 URL 可省略，Bot binding 保存独立 URL；解析优先使用 Bot 地址。
- 注册重放保留地址，显式不同地址返回冲突；Gateway 缺少有效地址时不写入 Bot。
- PATCH 支持保留、继承、替换，拒绝与能力混合更新；保留鉴权和禁用校验。
- Memory/DB 存储、SQLite 028 / MySQL 027 迁移、本地缓存失效和现有跨实例 TTL。
- WS 切换路径在写入或断开连接前校验有效地址；HTTP 发送失败不回退。
- Backend 未修改。按用户要求保留原文件布局，不处理本次触及的历史超长文件。

## 验证证据

| 验证 | 结果 |
| --- | --- |
| 改造前 Provider 兼容性基线 | 118 项通过 |
| `cargo check --manifest-path src/bcs/Cargo.toml --workspace --all-targets` | 通过 |
| 新增 DTO、Core、HTTP 管理、Memory/SQLite 测试 | 通过：DTO 5、Core 8、HTTP 管理 2、Store 3 项；均包含在全量回归中 |
| SQLite 新库、旧库升级、重复启动 | 通过 |
| 跨实例缓存 | 实测 31 秒后读取新地址，通过 |
| 新增三接收端 HTTP 集成 | 2 项通过：1.0 callback / 2.0 SSE，独立地址、继承、失败不回退、PATCH 后新流量 |
| 既有 `provider_downlink_integration` | 9 项通过 |
| `ci_test.sh --fast-fail` | 通过：无代理宿主环境 4,946 项通过、49 项跳过；退出码 0 |
| `arch-check.sh` | 未通过：4 项通过、5 项跳过、6 项失败；CFG-1 在宿主重跑 126 项通过，其他涉及依赖脚本、import/naming/conformance 检查 |
| `check-protocol-compat.sh` | SKIP：脚本仍引用不存在的 `crates/service-api/bcs-protocol/tests`，不计通过 |
| `check-r25-conformance.sh` | 架构总入口已执行该检查，返回 harness/条目失败，不计通过 |
| Singlebox 默认 all-module 门禁 | 已运行，BaaS 启动健康检查超时，未进入验收 |
| Singlebox artifact verifier | 已运行，报告缺失，未通过 |
| `git diff --check` | 通过 |

本地网络测试使用宿主执行环境，并清除该测试进程的 HTTP/SOCKS 代理环境变量。
没有修改产品代理配置或降低 CI 门槛。

## 未验证与评审

- MySQL 迁移已核对 nullable TEXT、SQL 参数与读写映射，未执行真实 MySQL 实例迁移。
- 未连接真实 Poolab 接收端；仍需验证其 BCN 报文和 Provider 下行鉴权兼容性。
- 独立只读评审未发现 Critical / Important 问题。
- 延后的小问题：非法 PATCH JSON 类型由既有 Axum extractor 返回 HTTP 422 纯文本，
  spec §4.3 写作 400。两者均拒绝写入，后续需统一文档或响应规范。

## 实施决策

- 契约、Repo 和调用方统一演进，避免中间提交无法编译；代价是功能提交较大。
- 用户明确取消文件拆分与行数限制处理；代价是保留历史超长文件。
- 新验收复用已有 tests/helpers 启动设施，独立编写接收端；代价是少量 fixture 重复。
- 独立评审检查未提交的 tracked diff 和全部新文件，同时运行全量验证；评审必须包含未跟踪文件。

日志：`/private/tmp/bcs-bot-webhook-ci-host.log`（全量单测）、
`/private/tmp/bcs-bot-webhook-live.log`（真实 HTTP）、
`/private/tmp/bcs-bot-webhook-config-host.log`（配置校验）、
`/private/tmp/bcs-bot-webhook-arch.log`（架构）、
`/private/tmp/bcs-bot-webhook-singlebox.log`（Singlebox）。
