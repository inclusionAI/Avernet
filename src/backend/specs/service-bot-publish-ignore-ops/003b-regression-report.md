# 服务 Bot publish-ignore 本地回归报告

本报告为首版历史证据。当前接口已取消version并复用共享绑定/连接解析，不能把下述历史结果当成本轮验证；本轮以009本地报告及当前PR head检查为准。

日期：2026-09-16。结论：本地功能与跨组件契约 PASS；远端 PR CI 以最终提交对应的实际结果为准。

执行者同时参与了 Engine 实现，本报告不冒称独立作者审查；独立交叉评审由 `review_implementation` 执行。

## 边界

- 既有链路：Backend 发布接口鉴权及精确版本绑定 → BaaS 固定副本或 ARCA 精确连接 → Engine bot HTTP router → 固定 ignore 文件。
- 允许新增：publish-ignore 请求、服务与协议、DI、运行身份只读字段、签名授权与固定文件更新、相应测试。
- 禁止触碰：tar、通用 HTTP 客户端、Relay、Cron 生产逻辑、发布状态机。本回归未修改这些组件。
- 本功能是通用 Engine HTTP 管理接口；本次使用真实 ASGI/router/DI/文件服务，未启动无关 Codex/Claude 容器。

## 可重复验证

在 `src/engine` 使用已安装 Python 3.12 环境：

```bash
uv run --no-sync --with pytest-cov --with pytest-asyncio --with socksio pytest src/engine/community/api/tests/test_publish_ignore.py src/engine/community/shared/tests/test_credentials.py src/engine/community/tests/architecture -q --cov=engine.community.plugins.publish_ignore --cov-report=term-missing
```

结果：44 passed；固定文件服务 139/140 行覆盖（99%）。覆盖 add/remove 幂等、并发不丢更新、CRLF/Unicode 文件名、路径拒绝、文件/锁 symlink、大小限制、原子替换失败保留原内容、Ed25519 篡改/过期/缺配置、锁等待超时及等待中签名过期、持久化请求防重放、DI 协议、日志字段及签名不落日志。

在 `src/backend`：

```bash
.venv/bin/python -m pytest tests/community/core/service_bot/test_publish_ignore_engine_contract.py -q
```

结果：5 passed。真实 Backend `HttpPublishIgnoreRuntime` 生成 Ed25519 签名，由真实 Engine HTTP router、DI 服务、凭据解析和文件更新处理：

1. BaaS 固定副本 add → 重复 add → remove → 重放原 add 拒绝，文件保持为空。
2. ARCA 精确连接执行同一链路；未将其视为 BaaS UUID。
3. BaaS 运行凭据 V4 与请求 V3 不符，失败且无 ignore 文件写入。
4. ARCA 相同身份错配拒绝。
5. 篡改路径及缺少签名拒绝，原文件保持不变。

设备枚举、连接 resolver 与 HTTP 传输是本地替身；签名、HTTP 路由/参数验证、DI 和文件操作执行真实代码。所有文件均限定在 pytest 临时目录；没有读取或修改真实 `/home/admin`，没有调用远端设备。

## 整套 Engine CI 观察

主编排执行的 `/tmp/publish-ignore-engine-ci.log` 已记录：2692/2692 passed、5 deselected，102.44 秒，总覆盖率 93.50%。未确认 Cron 卡死：该测试随后通过，不能将短暂输出停顿认定为 busy loop。

脚本末尾 `scripts/ci/report_check.py` 抛出类型注解 SyntaxError，是后处理选用了不支持注解的 Python 解释器；需要以同一 venv Python 3 重跑报告检查。未修改无关 Cron 生产代码；完整 CI 最终门禁由主编排核验。提交前变更行覆盖门禁记为 pending，不用整文件覆盖冒充变更行覆盖。

## 外部边界日志

- Engine：`engine.publish_ignore.request/success/failure`。请求含 request_id、expected_target、operation、path、system/direction/method/route；成功含结果和耗时；失败含安全错误码、类别和耗时。
- Backend：`backend.publish_ignore.engine_request/engine_response/engine_failure`，保留原始请求与每副本 request_id 的关联。
- Engine 测试断言记录的业务字段、成功结果、失败类型/耗时及已实际发送的签名不出现于日志。私钥只存在 Backend 配置，Engine 仅公钥。

## 限制

- 未进行真实 OSS/NAS 性能、NFS flock/rename 或远端设备重启验证。
- 临时 ASGI HTTP 验证不代替 BaaS/ARCA 真实网络黑盒和发布平台检查。
- 首次上线需要配置 Backend 签名私钥与 Engine 验证公钥，并具备 BOT_ID/ENTITY_ID/VERSION/STAGE 运行身份；缺少时失败关闭。
- 本地 PASS 不代表远端 PR/ACI 已通过，也不代表已经部署。
