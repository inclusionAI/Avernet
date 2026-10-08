# ignore 查询编码报告

日期：2026-09-18。本轮先生成 frontend-api.md，再实现查询。未提交、推送或部署。

## 范围与边界

既有链路：Backend publish-ignore router → service 权限/当前 binding 解析 → runtime BaaS/ARCA → Engine 固定 ignore 文件。
允许新增点：同 router 平放 GET、query 值对象/协议、只读实现与测试；仅提取原有权限/目标解析供 POST/GET 共用。
禁止触碰点：通用 resolver、数据库、发布状态机、daas 复制、前端业务代码和部署流程。原有签名移除、原地重启及其未提交变更均保留。

Worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`。
分支：`feat/service-bot-publish-ignore-ops-rel20260917`。

## 本轮改动

| 范围 | 文件（省略 community 前缀） | 用途 |
| --- | --- | --- |
| Backend HTTP | adapters/http/service_bot/router_publish.py | GET 参数和响应映射，复用管理身份 |
| Backend 契约 | kernel/publish_ignore.py、api/publish_ignore_service.py、plugin_api/publish_ignore_runtime.py | Query DTO 与 query 协议 |
| Backend 应用 | core/service_bot/services/publish_ignore_service.py | 共用授权/目标解析、逐实例汇总 |
| Backend 传输 | plugins/community/publish_ignore_runtime.py | 固定 BaaS 设备/ARCA 连接 GET，校验响应，保留部分成功 |
| Engine HTTP/契约 | api/bot/router.py、core/publish_ignore/models.py、protocol.py | GET、ExpectedTarget 与 request_id |
| Engine 文件 | plugins/publish_ignore.py | 固定路径一次只读快照，保序重复、计数及 SHA-256 |
| 测试 | Backend publish_ignore service/runtime/router/contracts/endpoints/factory/真实Engine契约，Engine api/tests/test_publish_ignore.py | 新增行为及已有 POST 回归 |

## 行为

- 支持 draft/verify/online，不指定版本；沿用可信管理权限与共享 binding resolver。
- 每个实例返回 paths/count/revision，不合并不同实例；不存在文件返回空列表，无实例/坏文件返回失败。
- 不创建 ignore、lock、journal；沿用 NOFOLLOW/NONBLOCK、普通文件及大小限制。
- GET 不要求独立签名密钥；复用既有运行时连接认证，并验证实际 Bot/entity/stage。

## 日志

Backend：query_request/query_response/query_failure、engine_query_request/engine_query_response/engine_query_failure。
Engine：query_request/query_success/query_failure。
记录方法、路由、系统、方向、身份与关联 ID、目标、业务结果、耗时；只记录安全错误码及异常类别，不输出连接认证头、Cookie 或原始异常文本。
paths UTF-8 总字节超过 4096 时，日志仅保留 entry_count/revision 与 paths_omitted；API 完整列表不变。相关测试验证普通列表、过大列表、异常与凭据哨兵。

## 验证进度

- Engine TDD 首轮新增 GET 测试在未注册时 405，实施后通过；审查补充通用异常日志和大列表摘要后，root 实跑 65 passed。
- Backend 编码 agent focused 112 passed；另新增 protocol GET 契约后该套件 13 passed。最终独立数量以 003/003b 报告为准，不将有重叠的运行求和。
- root 本轮执行原地重启部署链测试 6 passed，启动脚本标记/原地重启测试 24 passed。
- 独立 reviewer 已 PASS：Backend 113/113、Engine 65/65；新增 query 函数可执行行 91/91。强化日志断言后 runtime 另跑 24/24。root 另跑真实 Engine 与协议契约合计 38 passed。
- 最新 Engine 全量 2729 passed、5 skipped，总行覆盖率 87.71%；Backend 全量 18871 passed、43 skipped，总行覆盖率 89.12%。合计 21600 passed、48 skipped、0 failed；详细证据见 003-review-report.md、003b-regression-report.md。
- 本地测试不代表已部署或远端 ACI 通过；当前未提交工作区没有本轮可用的远端 base/head job，ACI 为 PENDING。
