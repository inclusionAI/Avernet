# Spike 阶段 0 报告 —— 轻量版（2026-10-13）

机器聚合自 evidence/*.json；真引擎/浏览器/性能实验尚未执行（标记 NOT RUN）。

## 门禁汇总

| 门禁 | verdict | 证据等级 | 证据文件 | 手机判定 |
|---|---|---|---|---|
| G2 | **pass** | toolchain | G2.json | a2a-sdk==1.2.2 锁定 + API 面备忘 |
| G1a | **pass** | protocol-contract | G1a.json | SDK 1.2.2 Card 驱动 9 事件流全绿（6 检查项全 true） |
| G1b | **partial**（lite） | protocol-contract（shim） | G1b-lite.json | 8 检查项全 true（JWT 鉴权/SSE 透传/恢复完整性）；正式 proxy + Playwright 浏览器 deferred |
| G3 | NOT RUN | — | — | 需 singlebox 真引擎（openclaw）通过 bridge 活管线 |
| G4 | NOT RUN | — | — | 需双客户端旁观注入链路 |
| G5 | NOT RUN | — | — | 真实性能基准（SSE vs WS 冷连接 P50/吞吐/RSS） |

## G1a 详情（SDK 1.2.2 Card 驱动全流）

| 检查 | 结果 |
|---|---|
| first_event_is_task | true |
| artifact_update_present | true |
| every_event_exactly_one_member | true |
| last_event_is_final_status | true |
| last_final_status_is_terminal | true |
| seq_strictly_increasing | true |

SDK 用法：`ClientFactory(ClientConfig(streaming=True)).create(card)` → `send_message(SendMessageRequest)` →
`AsyncIterator[StreamResponse]`，经 `WhichOneof("payload")` 判定 task/message/status_update/artifact_update。

## G1b-lite 详情（proxy shim 链路）

| 检查 | 结果 |
|---|---|
| no_token_401 | true |
| good_token_200 | true |
| stream_200 | true |
| at_least_5_events | true |
| events_not_single_batch | true |
| final_present | true |
| replay_seq_sorted | true |
| replay-reaches-final | true |

 shim 复刻 WS eth 面代理 + HTTP 面代理 + 中间人代理（JWT/SSE/401），不复刻 resolver/MOSN/LB。

## Mock 测试

| 层 | 结果 |
|---|---|
| 全文 python 块编译（30 块） | 0 failed |
| harness 装配（29 文件） | 0 syntax failures |
| pytest | **55 passed** |

## 真实取证发现的协议级知识（已回填设计文档）

| # | 发现 | 影响 |
|---|---|---|
| 1 | A2A v1.0 protobuf schema 的 TaskStatusUpdateEvent 仅有 taskId/contextId/status/metadata 四字段 | `final`/`lastSeq`/`seq` 必须走 `metadata` 扩展（顶层会被 ParseDict 严格拒绝） |
| 2 | protobuf `MessageToDict(use_integers_for_enums=True)` 将 TASK_STATE_COMPLETED 转数字 | 客户端解析需保留枚举名（`use_integers_for_enums=False`） |
| 3 | a2a-sdk 1.2.2 客户端 API 与 v0.x 不兼容 | `send_message(SendMessageRequest) -> AsyncIterator[StreamResponse]`（非旧版 `send_streaming_message(payload=)`） |
| 4 | ocb proxy 依赖 SOFAPy + Layotto/MOSN runtime | 独立起不动——正式链路须 singlebox 全家桶 |

## 放行条件

阶段 1a 放行 = G1a **pass** + G1b **pass**（含正式 proxy + 浏览器） + G3 **pass** + G5 **pass**
+ pytest 全绿 + 无 NOT RUN 门禁。

当前状态：G1b 正式部分 / G3 / G4 / G5 尚未执行——NOT RUN 即不放行。

## 处置

| 项 | 状态 |
|---|---|
| 轻量路线（shim proxy + httpx 探针） | **可继续** |
| 正式路线（singlebox 全家桶 + 真引擎 + Playwright + 性能基准） | 待启动 |
| 进入阶段 1a（产品实现） | **待 G1b-G5 正式门禁结果** |
