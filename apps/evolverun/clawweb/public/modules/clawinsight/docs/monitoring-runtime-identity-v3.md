# Monitoring Runtime Identity v3

更新：2026-10-22。状态：监控侧已实现，本地测试覆盖；**尚未完成新版自愈服务联调，不是上线确认**。
自愈对接依据为双方确认的 `session-recovery-runtime-identity.md`；本文记录监控侧实现与交付边界，不重新定义自愈业务规则。

## 1. 完整数据链路

```text
同实例 .credentials（UTF-8）
  → CV dict[str, str]
  → diagnosis-job-v2.locator.runtime_identity（入队冻结）
  → diagnosis-event/v3.runtimeIdentity（JSON 对象）
  → 诊断表 runtime_identity（TEXT 中的 JSON）
  → 自愈创建请求 target.runtime_identity（完整对象）
```

这是格式转换，不是挑选定位字段。TOKEN、CLIENT_ID、OWNER_ID、BOT_ID、ENTITY_ID、
ENTITY_TYPE、BOT_TYPE、AGENT_CODE、ADMINS、STAGE、VERSION **全部保留**，也保留扩展字段。
空 `ADMINS=` 转为 `"ADMINS": ""`；ID 和版本等始终为字符串，不转数字。
只在数据库边界做 JSON 字符串编码/解码，网络上的字段不是 JSON 字符串。
`traceId` 不承担运行身份；监控 bot/entity/env、eventId、delivery key 与调用者身份不改。

## 2. 版本、兼容和失败行为

| 任务/事件 | 行为 |
| --- | --- |
| 有远程映射的新 OC 任务，读取成功 | locator 有完整对象；产生 v3 |
| 有远程映射的新 OC 任务，读取失败 | locator 显式 null；产生 v3，诊断继续，跳过自愈 |
| 历史 OC 任务，无 locator 标记 | 保持原版本和原行为 |
| TE 或无远程身份映射的本地投影 | 保持原逻辑，不产生 v3 |
| 已经进入 outbox 的事件 | 重发原载荷，不读凭据、不重写版本 |

v3 的 `schemaVersion` 为 `claw-monitoring/diagnosis-event/v3`，在 v2 完整字段集上增加
**必需但可为 null** 的 `runtimeIdentity`。非 null 必须为非空的平铺字符串字典。
v2 不允许新增此字段；bot-check 不升级。所有原有事件大小、时间和身份约束继续生效。
新 OC 的 null 标记不能按真假判断退回旧版本，也不能改成不带身份的自愈请求。

ACK 结构不改：v3 与 v2 一样校验 `botId/entityId/env` 三元组。
发送端按该三元组恢复本地 bot 绑定，避免把远程 botId 当成本地采集键。
服务端重放比较规范化后的对象：键顺序变化不冲突，值变化冲突。

## 3. 文件读取与快照

- UTF-8 解码，逐行读取，跳过空白行，以第一个 `=` 分出 key 和 value。
- 不 trim 值，不处理 shell 引号/变量/转义，不 `source` 或执行文件。
- 只拒绝缺少 `=`、空 key、重复 key、空文件及不合法 UTF-8 等无法可靠转换的输入。
- 文件有界读取：16 KiB；紧凑 UTF-8 JSON 对象上限：32 KiB。超限不截断。
- 使用非阻塞打开并检查普通文件，避免 FIFO 阻塞；失败仅记录固定原因码。
- 不实现 STAGE 枚举、设备正则、版本或归属业务校验；这些由自愈服务负责。

运行时实际持有 `SessionView`，**不是带 root 的 `DiscoveredFile`**。
通过 `view.source_id` 取得当前配置的 source root，结合 `view.path`、`view.relative_path`
及 `InventorySettings.session_directory_suffix` 定位同实例根目录下的 `.credentials`。
验证路径一致性、后缀和目录包含关系；拒绝跨实例逃逸，不扫描其他实例兜底，
不从目录名拼设备 ID。自定义后缀使用既有配置。

只在 OC 确认需要新建诊断任务且启用 reporting 时读取，放到异步线程执行。
原子入队后 locator 随任务持久化；worker、重启恢复和 outbox 重试都使用已保存值。
读取失败写 locator null 和 SOFT 原因码，不把文件内容放入模型 snapshot/evidence。

## 4. 落库与保密

诊断表新增的 `runtime_identity` 必须为可空 TEXT，用户已在生产表加列。
本改动只更新开发/测试建表定义及只读 schema checker，**不执行生产 ALTER**。
生产列类型、权限和容量仍需部署方只读核验；本次未连接生产库。
v2 和 v3 null 存 SQL NULL，v3 对象存 JSON；历史 v1 行仍按原兼容 v2 读取。
损坏的 v3 数据库存储不降级成可用身份，沿用存储错误路径。

任务快照、outbox 和诊断数据库是完整凭据的受保护存储，需沿用受限文件权限、
内网访问控制及数据库最小权限，备份同样按敏感数据处理。
普通日志、模型输入和前端 DiagnosisItem 均排除完整身份；ACK 不回显身份。
不通过删字段或改写 TOKEN 达到脱敏，这会破坏真实调用载荷。

## 5. 自愈调用方边界

保留原 ALERT/NOT_COMPLETED 条件、白名单、诊断文本、幂等 key 和先入库后触发顺序。
新 v3 OC：

1. runtimeIdentity 为 null、BOT_ID/OWNER_ID 缺失或为空、BOT_ID 与 event.botId 不同：跳过自愈。
2. `target.bot_id = runtimeIdentity.BOT_ID`。
3. `target.entity_id = runtimeIdentity.OWNER_ID`，不是 ENTITY_ID。
4. env/engine 来自原事件，`target.runtime_identity` 是完整对象。
5. 认证上下文仍为 `{ userId: event.entityId, isAdmin: false }`，不伪造为 OWNER_ID。

沿用进程内 `parseRequest → service.create`，不新增 HTTP 绕行、不绕过解析器、不修改
`repair/session-recovery` 内部代码。对端 409/422 不通过删身份、换设备或换版本重试；
临时错误沿原有重试机制发送同一载荷。诊断入库不会因后续自愈拒绝而回滚。

> 集成说明：本节以下记录诊断分支原始基线的状态。与自愈运行身份改动整合后，真实解析器已接受完整对象，
> 仅将阶段、设备和版本保存到自愈任务；HTTP → 真实解析器 → 任务存储及重复投递的测试位于
> `server/routes/__tests__/monitoring.test.ts`。部署环境的真实端到端验收仍需另行执行。

**原始基线的集成阻塞：**本分支基线里的真实自愈解析器仍不接受 `target.runtime_identity`。
新 v3 OC 请求会在解析处安全跳过（固定原因 `invalid_recovery_input`），不会实际创建自愈任务。
消费方边界测试使用单独标明的未来解析器 mock，只证明调用方组装和错误处理，
不代表真实新版自愈端到端已经成功。新版解析器交付后，需把旧解析器拒绝测试更新为正式接受测试。

## 6. 发布与回滚

1. 部署方只读确认已有 nullable TEXT 列；不要重复加列。
2. 先部署能接收 v2/v3 的 clawweb，并集成另一位开发人员的新自愈契约。
3. 联调完整对象、202/409/422、draft 空 VERSION、发布阶段缺失/空 VERSION，
   以及 OWNER_ID != ENTITY_ID 的授权与结果关联；不修改 actor 规避问题。
4. 上述通过后才灰度开启新的 OC 上报，不提前全量切换 CV；新 OC null 会跳过自愈。
5. 回滚采集端时保留接收端 v3 能力和列，先排空既有 v3 outbox；不删列、不重写历史载荷。

真实生产 ODC 写读和真实新版自愈联调仍待执行；本地 SQLite/HTTP loopback 和 mock 测试不替代它们。

## 7. clawweb 实现与验证入口

路径相对本模块：

| 文件 | 变更 |
| --- | --- |
| `server/services/monitoring/contracts.ts` | v2/v3 判别联合，保留 v2 常量，DTO 排除完整身份 |
| `server/services/monitoring/validation.ts` | 严格版本/字段验证，完整对象规范化，安全保留扩展键 |
| `server/repositories/monitoring-repository.ts` | TEXT JSON 往返、v1/v2 兼容、幂等与运行时 DTO 剔除 |
| `server/repositories/monitoring-schema-check.ts` | 只读检查 nullable TEXT |
| `server/services/monitoring/schema.ts` | 开发/测试建表列 |
| `server/services/monitoring/session-recovery-trigger.ts` | 完整 target 组装、安全跳过、不改认证上下文 |

在本模块目录、已配置工作区依赖下运行：

```bash
pnpm run check
pnpm run test:monitoring --pool=forks --maxWorkers=1
```

本机验证使用与 better-sqlite3 ABI 匹配的 Node 24，直接执行 TypeScript `tsc -p tsconfig.json --noEmit`
和 Vitest `run --config vitest.monitoring.config.ts --pool=forks --maxWorkers=1`。
不要使用与已安装原生扩展 ABI 不一致的 Node；没有升级或重新安装依赖。

测试包括 v2/v3 边界、完整对象/扩展字段、对象键序幂等、重开真实 SQLite 后读取、
SQL NULL、历史 v1/v2、坏 JSON、缺列、POST ACK/GET 不泄露、监控 entity 不变和诊断先入库。
`runtime-identity-recovery-boundary.test.ts` 是**mock 新版解析器的调用方测试**；
`session-recovery-trigger.test.ts` 另行验证本基线真实旧解析器拒绝新字段且不调用服务。
二者不能合并解释为已经通过真实新版自愈集成测试。

### 本次本地结果（2026-10-22）

TypeScript noEmit 检查通过；monitoring Vitest **10 个文件、234 个测试通过**，
使用实际 better-sqlite3 测试存储。未连接生产数据库，未执行真实新版自愈端到端调用。
