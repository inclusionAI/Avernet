# v4 代码核查附录：TE 支持究竟落到了哪里

> 日期：2026-09-08；本地源码基线见主方案。本文保留 v4 阶段的源码审计与离线验证记录，不是线上运行认证。
> **v5 阅读修正：**实施范围以主方案为准。下文的独立调度、持久重试队列和查询投影是历史审计建议，不是本期默认全部采用。页面只展示诊断，不展示完整对话原文；固定 user_turn=1 不代表 TE 划分错误，也不要求为页面补轮次序号。
> [返回开发方案](clawinsight-monitoring-integration.md)
> 路径前缀：`CV` = claw-validation-dev_murao；`INSIGHT` = Avernet/src/evolverun/clawweb/public/modules/clawinsight。

## 1. 本轮变更边界

`git diff --stat 66eaf47..3ffb5fd`：22 个文件，883 行新增、51 行删除。重点提交：

| 提交 | 内容 |
|---|---|
| `c64ddfb` | TE AI Vision Trace 监控入口 |
| `d3fc521` | Trace 解析与轮询重试修正 |
| `2a8a2b3` | detail 保留期过期、超大 Trace 的跳过处理 |
| `342b450` | adapter 先构造记录，再建立 boundary |
| `a090dea` | TE 历史回放、通知 Trace 元数据 |
| `3ffb5fd` | TE 测试改为 unittest，更新入口文档 |

该范围没有新增 HTTP 查询服务，也没有给 SQLite 新建专用 TE source/trace 状态表；TE 进度复用 metadata，任务仍复用 diagnosis_job。

## 2. 建议按这个顺序阅读 claw-validation

### 第一步：谁被监控 —— `online_config.py`

关注 `BotEngine`、`MonitoredBotConfig`、`load_online_config()`：

- engine 只接受 `OC/TE`。
- 每个 Bot 只接受 `bot_id/engine`；重复 bot_id 被拒绝。
- `ai_vision` 是顶层共享配置，不是每个 Bot 一份；TE 需要它存在。
- 目前没有 bot_name、environment、monitorId、按 Bot 的 NAS 映射或独立 TE poll interval 配置。
- 无 bots 的旧配置仍可工作；`configs/online-monitor.selected-bots.example.yaml` 中新增段默认是注释。
- loader 允许 `.private` 配置中的字面 API Key；公开配置和新部署仍应走环境变量/Secret，不在方案复制任何真实值。

### 第二步：怎么接到旧系统 —— `application/online_monitor.py`

`OnlineMonitorApplication.run()` 先创建 SQLite、模型 runner 和 OC inventory，再筛出 TE Bot 创建 `TEPollingService`，最后把 poller 注入公共 `OnlineMonitorService`。

这说明当前是“增加一条采集支路”，而不是一套完全替换 OC 的引擎路由框架。OC 清单仍由原 inventory patterns 控制。`application/online_monitor.py::_run_monitor_loop()` 使用共同 poll interval；TE 接入没有独立的 30 秒调度器。

### 第三步：怎么获取 TE 数据 —— `monitoring/aivision/client.py` / `parser.py`

- `list_traces()` 使用 `resource.attributes.ant.agent.id = bot_id` 过滤。
- `get_trace_detail()` 请求单条 detail；parser 从 `response.data.spans` 构造 `TraceSpan`。
- parser 保留 Span attributes/resource，但“解析到了”不等于“完整送进诊断证据”。
- 列表 parser 返回 TraceSummary 元组，没有把 `meta` 分页信息传给 poller。
- 错误类型在 `errors.py` 定义；运行时状态还没有完整保留这些具体错误分类。

不要让 ClawWeb 再写一遍这些客户端；网页只读诊断服务接口。

### 第四步：诊断何时产生 —— `monitoring/aivision/adapter.py::TETraceAdapter.to_snapshot()`

当前真实映射：

```text
source_id             = "aivision:" + sha256(bot_id)[:16]（默认参数下）
instance_id           = "aivision-agent:teclaw"
bot_id                = 传入的配置 Bot ID
session_id/session_key = detail.session_id 或 detail.trace_id
conversation_round_id = "trace:" + detail.trace_id
user_turn             = 默认 1，在线 poller 未另外传入
locator               = engine、source_kind、trace_id、source_session_id、bot_id
```

与 `docs/ai-vision-te-realtime-monitoring.md` 的目标说明有这些差别：

| 文档意图 | 当前实现 |
|---|---|
| source_id 包含 Space/Product 域 | 默认 source_id 仅按 Bot 哈希；不能直接支持多个 Space 下同 Bot 的隔离 |
| 校验 detail Trace ID 和每个 Span 的 Bot 身份 | 当前调用链没有对应的显式一致性验证 |
| 已确认结束才构造 snapshot | 没有 outcome/完整结束 gate；空 Span 也能被组装成确认结束 |
| 按结构化 Span kind 映射工具/LLM | 主要靠 Span name 子串识别工具与失败；存在证据误解风险，不能据此直接断言必然误告警 |
| input/output、LLM messages、工具参数/结果进入证据 | 主要保留 input、Span 名称/status；有 Span 时 output 不进入 conversation_records |
| 截断要反映不完整 | 截断 Span 后仍标记 COMPLETE |
| 真正的 Session fallback 标记 | 缺失时直接用 traceId，未记录来源类型 |
| 同 Session 持久化发现序号 | 当前固定 user_turn=1；页面不展示 Session 内轮次，所以不按此提出划分改造 |

**口径纠正：**按业务对齐，一条 AI Vision Trace 对应一轮对话和一个诊断单位。`user_turn=1` 不能表示同 Session 内的递增序号，但不能据此认定分轮错误。不同 Trace 不合并，本期不补序号。

公共 `boundary_id()` 位于 `monitoring/loop_end/detector.py`，摘要材料含 identity、generation_id、loop_end_record、loop_state。因此同 Trace 增加 Span 或停止状态变化可能得到不同 boundary；**只有重复同等 snapshot 时的数据库幂等，不能替代稳定 Trace 唯一键**。单独改变结束时间不会改变该 ID。

### 第五步：为什么需要重新定义水位 —— `monitoring/aivision/poller.py::poll_once()`

- 默认 bootstrap 300 秒、overlap 120 秒、100 条/页、最多 20 页。
- 首次 cursor 先取 now - bootstrap，再减 overlap，实际初始起点是 now - 420 秒，而不是刚好五分钟。
- metadata key 是 `te.watermark.<bot_id>`。
- `newest` 在 detail 读取前就按列表时间更新；页失败、detail 失败、达到页上限后仍走 set_metadata_value。
- 空轮询也写 newest；由于 newest 初始化为已减 overlap 的 cursor，连续空结果可能使该值向前回退。它既不是可靠心跳，也不是严格的“已完整扫描到此时间”。
- detail 最多即时重试 3 次，仅对 retryable 异常；没有跨重启的失败 Trace 重试队列。
- 404 和 parser error 只计 skipped；不是已持久化的跳过记录。
- 去重发生在 adapter 后的 enqueue_job；每次重叠查询还会获取已处理 Trace 的 detail。
- 多 Bot 串行轮询，运行时也在同一 poll_once 内 await TE；慢请求可能延长 OC 下一轮间隔。

可直接复用：client、parser、公共 queue/worker。按 Bot 状态和准确记录归属属于页面必要保障；水位、重试与幂等问题作为 CV 质量回归核实并按影响定向修复。是否需要独立调度/持久重试队列，不能仅凭本审计默认决定。

### 第六步：数据落在哪里 —— worker / storage / diagnosis models

| 文件/符号 | 当前作用 | 查询设计注意点 |
|---|---|---|
| `monitoring/runtime/diagnosis_worker.py::_process()` | 读取 v2 payload、公共 TC 诊断、存结果、条件通知 | locator 未整体写入 TurnDiagnosis；通知新增 engine/trace_id |
| `monitoring/diagnosis/models.py::TurnDiagnosis.to_dict()` | identity、boundary、分类、文本序列化 | 可读取 conversation_round_id、loop_end_timestamp；不是只有主表列可用 |
| `monitoring/storage/sqlite_repository.py::store_diagnosis()` | 写 diagnosis_result | bot_id 来自 diagnosis.identity；没有独立 engine/trace_id 列 |
| `…::enqueue_job()` | boundary_id 主键、INSERT OR IGNORE | 原有 job 幂等不等于跨变化 Trace 幂等 |
| `…::diagnoses()` | 全量读取并反序列化 | 不适合作为 Web 分页实现 |
| `…::__init__()` / `_create_schema()` | 可写连接、建表、WAL | 新只读 API 不能直接复用构造副作用 |
| `monitoring/lifecycle/identity.py::resolve_session_identity()` | OC Session 身份解析 | Bot ID 返回 view.instance_id，需单独业务映射 |

可按 boundary_id 关联 diagnosis_job 读取 locator；长期需要的定位字段应保存在诊断可查询数据中，避免历史可读性绑定到队列保留策略。v5 优先现有结果表加必要字段，不强制新增查询投影表。

### 第七步：状态和历史 —— runtime / scripts / replay

- `monitoring/runtime/service.py::_write_status()`：进程级 job counts、last_poll_at、last_diagnosis_at；没有 per-Bot heartbeat。
- `TEPollReport` 中的 traces_seen/source_errors 是当轮汇总；skip 计数未完整传入公共状态文件。
- `scripts/run_daily_online_monitor.sh`：默认到次日北京时间零点结束；外部负责后续再次启动。
- `scripts/start_online_monitor.sh`：无条件挂 NAS、恢复到 /tmp、退出后备份，备份成功/失败都可能删除运行库；没有周期备份。
- `scripts/checkpoint_sqlite.py`：已有 SQLite 一致性备份/恢复辅助能力，可用于改造，但辅助函数存在不等于已部署周期任务。
- `application/historical_replay.py::_execute_te()`：历史窗口 list/detail → 同一 worker；按 start_time 过滤半开区间；异常累计 skipped。存在 TE Bot 时直接返回该分支，没有同时完成 OC 回放。

## 3. Avernet / OCB 的接入证据

| 路径（相应仓库内） | 已核对的事实 | 本期用法 |
|---|---|---|
| INSIGHT `web/pages/InsightCenter/index.tsx` | 页面本身是三个常规治理 Tab，加管理员 Tab；存在治理数据加载副作用 | 加 module 维度，将旧治理页与监控页的加载隔离 |
| OCB `src/evolverun/clawweb/internal/bootstrap/clawweb/src/App.tsx` | 持有全局 nav，lazy import InsightCenter，挂 `/insight` | 不在 MonitoringPage 再画全局栏 |
| INSIGHT `server/services/insight/insight-runtime.ts` | 现有治理依赖数据库与 evidence provider；支持 fixture | 参考模式另建 monitoring runtime，不耦合治理 DB readiness |
| INSIGHT `server/services/repair/ocb-gateway.ts` | 外部 HTTP 客户端范式 | 复用设计风格，不复用修复动作 |
| INSIGHT `server/index.ts` | package 的稳定导出入口 | 导出新 runtime/router 与最小契约 |
| OCB `…/internal/bootstrap/clawweb/server/index.ts:791` | `/api/insight/v1` 的真实挂载处 | 增加一次 monitoring router 装配 |
| Avernet `src/evolverun/clawweb/public/modules/clawevolve/server/repositories/evolve-repository.ts::listEvolveBots()` | 查询 ac_bots 的 owner/entity 范围，按 Bot+env 去重 | 名称能力有基础，但不能直接充当共享固定清单接口 |
| OCB `…/internal/bootstrap/clawweb/server/private/config-mist-runtime.ts` | 内部 Secret 注入边界，指南指定扩展位置 | 加独立 Token 映射；不让业务模块直接访问 MIST |

指南明确 package 组合而非源码覆盖；内部 Host 接线允许改 OCB，但监控业务 Owner 仍在 Avernet。当前方案也不把独立 CV 的内部采集细节开源复制过去。

## 4. 本轮实际验证

### 4.1 仓库现有测试

首次使用系统 Python 3.9：

```sh
PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.unit.test_aivision_parser tests.unit.test_aivision_adapter \
  tests.unit.test_online_config tests.unit.test_dingtalk_notifications \
  tests.integration.test_diagnosis_storage tests.integration.test_historical_replay -q
```

结果：**26 项通过**。TE parser/adapter 本身现有覆盖较薄；通过不等于完整满足上游 TE spec。

追加 OC e2e / runtime / checkpoint / status 四模块时，19 项中出现 **2 个 Python 3.9 兼容错误**：`Path.stat(follow_symlinks=False)` 在当前解释器不可用，位置为 inventory_scheduler.py:413。没有修改业务代码规避；随后改用本机已安装的 Python 3.12.14 复验。

Python 3.12.14 首次复验因缺少 PyYAML 导致部分模块导入失败。将本机已有 PyYAML 所在目录追加到该命令的 PYTHONPATH 后，以上六模块加四个追加模块合计 **45 项全部通过**（1.529 秒）；未安装依赖或改动系统默认 Python。这不是干净环境依赖验收，部署时仍应在固定解释器的隔离环境内安装项目依赖。

追加模块：`tests.e2e.test_online_monitor_e2e`、`tests.e2e.test_online_runtime_optimizations`、`tests.tooling.test_checkpoint_sqlite`、`tests.tooling.test_runtime_status_script`。

仓库 `pyproject.toml` 仍声明 Python >=3.9，与本次观察到的 OC 路径兼容性不一致；本轮仅记录，正式部署需明确受支持版本并在干净环境重测。

### 4.2 离线合成数据检查

本轮使用内存构造的 Trace、假 client、假 metadata repository；未访问真实 Bot、AI Vision、模型或发送钉钉消息。观察到：

| 检查 | 结果 |
|---|---|
| Span 没有 end，能否被 adapter 标记确认结束 | 可以 |
| Span resource 的 Bot 与传入配置不一致，入口是否拒绝 | 当前没有拒绝 |
| 有 Span 时，trace output 是否出现在 conversation_records | 未出现 |
| 工具 result attribute 是否出现在 conversation_records | 未出现 |
| 同 Trace 只改变 end 时间，boundary 是否变化 | 不变 |
| 同 Trace 增加一个 Span，boundary 是否变化 | 变化 |
| detail 失败后，metadata watermark 是否仍推进 | 可以推进；source_errors=1 |

这些验证仅用于确认源码行为与设计说明的差异，**没有据此声称线上已经发生串数据、漏诊或误告警**；风险需要在联调环境补成正式回归用例。

### 4.3 未执行的验证

未进行真实 AI Vision/模型/钉钉调用、AIStudio 入站网络与持久卷验证、生产数据检查；没有修改或部署 API、React 页面、OCB 接线。未运行 Avernet/OCB 构建与线上冒烟，因为本轮只更新 Markdown 设计。

测试建议：把 4.2 中的期望修正补成持久化回归，增加 TE poller/client/config/worker 的跨模块 fixtures，以及源错误、分页上限、重启重试、多人权限与数字页码的契约测试。v5 首版使用普通页码分页，不引入快照跳页服务。
