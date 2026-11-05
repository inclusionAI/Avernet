# Subworkflow fanout：首版使用契约

首版将一个对象数组展开为独立 child run，限制并发，并收集每项结果。
配置/状态以 `src/executors/subworkflow-fanout-types.ts` 为准；发布锁见 `src/workflow-dependencies.ts` 和 `src/controller/subworkflow-dependencies.ts`。
OpenClaw 与 MCP 已接入 durable API host；下文列出运行前提，不代表所有平台均已验证。

## 1. 配置

父节点示例；`plan` 是父流程中已有的节点，其输出 `items` 必须是对象数组：

```yaml
- id: run-experiments
  title: 批量实验
  phase: P5
  dependsOn: [plan]
  executor:
    type: subworkflow
    workflowId: run-one-experiment
    version: pinned
    fanout:
      schemaVersion: fanout.v1
      items: "{{nodeOutput.plan.items}}"
      itemIdFrom: [scene, partition]
      concurrency: 2
      successPolicy: all_done
      finishMode: wait_all
      resultPolicy: collect_all
      groupTimeoutSeconds: 1800
      cancelGraceSeconds: 0.03
    params:
      scene: "{{item.scene}}"
      partition: "{{item.partition}}"
```

- 所有示出的 `fanout` 字段均必填；三个策略值固定，不提供其他策略组合。
- `items` 可为一个完整模板引用，或静态对象数组，例如 `[{scene: a, partition: p1}]`。
  不接受拼接字符串、JSON 字符串数组或模板 fallback；动态值在运行时解析一次并快照。
- `itemIdFrom` 是非空且不重复的字段名数组；每项对应字段须为非空字符串。
  复合身份不得重复，静态数组立即校验，动态数组在首次创建 group 时校验。
- `concurrency` 为正安全整数；两个时长为有限正数秒，允许小数，示例不是生产建议值。
- 空数组合法，结果为 `businessOutcome: empty`，不创建 child。
- 未知 `fanout` 字段直接报错；`count`、`any_success`、`cancel_remaining` 均不支持，`executor.onFailure` 只能省略或为 `fail`。

## 2. 显式发布依赖锁

在父 `WorkflowSpec.subworkflowDependencies` 中按 child workflow ID 保存完整发布快照：

| 字段 | 含义 |
| --- | --- |
| `workflowId` | 必须与 map key、`spec.id` 一致 |
| `publishedVersion` | 发布记录中的正整数版本，不是父版本或当前 saved 版本 |
| `releaseId` | 发布身份；history helper 使用精确的 `deploy/<workflowId>/#<deployNumber>` tag |
| `digest` | `digestWorkflowSpec(spec)` 生成的 `sha256:<64位小写十六进制>` |
| `spec` | 完整 child `WorkflowSpec`，包括它自己的依赖锁 |

不要手填示例 hash，也不要仅凭 saved YAML 的 `version` 声称它已发布。
canonical JSON 排序对象键、保留数组顺序并忽略对象中的 `undefined` 可选字段；不删除 UI/下划线字段，拒绝循环对象和非 JSON 值。
若快照存在顶层 `spec.version`，它必须与 `publishedVersion` 一致。

以下 TypeScript 用于 taskguard 根目录的集成脚本；调用方传入 repository、父 spec 和明确选择的 `deployNumber`，不从 latest 猜测：

```typescript
import type { WorkflowSpec } from "./src/types.js";
import type { IDeployHistoryRepository } from "./src/db/repositories/types.js";
import { dependencyFromPublishedHistory, freezeSubworkflowDependencies } from "./src/controller/subworkflow-dependencies.js";

export async function lockChild(
  parent: WorkflowSpec, history: IDeployHistoryRepository, packId: string, childId: string, deployNumber: number,
): Promise<WorkflowSpec> {
  const row = await history.findByDeployNumber(packId, childId, deployNumber);
  if (!row) throw new Error("Selected child release is unavailable");
  const lock = dependencyFromPublishedHistory(childId, row);
  return freezeSubworkflowDependencies({ ...parent,
    subworkflowDependencies: { ...parent.subworkflowDependencies, [childId]: lock },
  });
}
```

helper 只接受可核验版本、deploy number、tag 和 JSON 快照的 `action: deploy` 记录；
`edit`、`save`、`pull`、`migration`、`rollback` 不能直接作为该 helper 的发布证明。
发布版本取 `row.version`：history 的 `specJson` 可能已删除顶层 `version`。
每个锁定 child 的嵌套 subworkflow（包括旧单子流程引用）都必须拥有完整下一层锁。
验证覆盖 loop body 和 node templates，拒绝身份/hash 不匹配、缺锁、环和同 ID 的冲突版本；
显式 `packId` 引用也须与 child 快照中的 pack 身份一致。

CLI `handleDeploy` 在远端保存前调用 `freezeSubworkflowDependencies`，要求已有显式锁；
不会自动选择“当前发布版”，更不会降级为 saved。保存响应必须保留锁和 pinned 调用契约，
随后还会精确回读发布历史；写入失败、冲突或快照变化不能报告发布成功。
发布失败可能已经留下 saved 内容；保存成功不等于发布成功，也不承诺跨 Git/HTTP 的原子回滚。

controller 初始化运行时把 `snapshotWorkflowDependencies(workflow)` 的副本存入同名 FlowState 字段。
派发、排队恢复和重接使用 `resolvePinnedWorkflowDependency(state.subworkflowDependencies, childId)`；
缺锁报 `DEPENDENCY_LOCK_INVALID`，不查询 latest、不取本地文件、不继承父同号版本。
该 helper 返回独立副本；运行版本使用 `publishedVersion`，不要修改持久锁中的 hashed `spec`。

## 3. 子流程导出与失败分类

child 必须显式声明顶层导出，成功时对应值必须是可 JSON 序列化的对象：

```yaml
outputs:
  result:
    from: nodeOutput.finalize-one
```

`finalize-one` 必须是 child 的真实产出节点。runtime completion 的 `outputs` 是导出映射，
collector 取 `outputs.result` 写入该 item 的 `result`，不额外包成 `result.result`，
不以整个 `workflowData` 兜底。成功但缺少/错误导出是 `CHILD_EXPORT_INVALID` 契约失败。

脚本要声明**已确认的 business/execution 失败**，必须返回以下 JSON；`category` 也可为 `business`：

```json
{"success":false,"fanoutError":{"category":"execution","code":"EXPERIMENT_FAILED","message":"Confirmed terminal failure","acceptanceKnown":true}}
```

runtime 从持久化节点结果读取该证据，转换为 `status: failed` 与结构化 `error`，供 `all_done` 汇总。
`retryable` 可选、`sourceNodeId` 由 runtime 补入；标记可重试不会开启首版自动重试。
所有实验都确认失败时，group 仍可能 `succeeded`，业务结果为 `all_failed`。
网络错误、查询失败、启动受理不明，或缺少 `acceptanceKnown: true`，都不能冒充确认失败。
即使 child 已标为 failed，缺少上述证据仍保持 unknown；不得仅因脚本退出就认定外部动作未受理。
`FanoutRuntimeError(detail, "unknown")` 保留同一 child ID 与并发占位，恢复只重接，不盲重放副作用。
明确未创建可用 `"rejected"` 使 group 失败；普通异常也必须传播，不能伪装成业务失败。
contract/authorization/persistence/orchestration 等错误不是可容忍业务失败。
取消请求成功或观察用 AbortSignal 中止，都不等于 child 已终止；取消须等待持久确认。

## 4. 持久状态与结果

`FlowState.subworkflowFanouts[groupNodeId]` 存储 `FanoutState`，不是 attempts 树：

| 位置 | 主要字段 |
| --- | --- |
| group | `schemaVersion`, `kind: subworkflowFanoutState`, `parentFlowId`, `groupNodeId`, `groupExecutionId`, `revision`, `status` |
| 时间/快照 | `createdAt`, `deadlineAt`, `cancelRequestedAt`, `cancelDeadlineAt`, `sealedAt`, `config`, `childWorkflow`, `inputDigest` |
| **flat `children[]`** | `itemId`, `itemIndex`, `attemptNumber: 1`, `childFlowId`, `sessionKey`, `stateDirRef`, `depth`, `params`, `item`, `status`, `result`, `error` |
| 封存 | `result`, `error`, `cleanupPendingChildIds`；终态后清理不得改写已封存结果 |

child 过程状态包括 `queued`、`dispatch_pending`、`dispatch_unknown`、`running`、`cancel_requested`；
child 终态为 `succeeded`、`failed`、`cancelled`、`timed_out`、`rejected`。
group 状态为 `running`、`cancelling`、`succeeded`、`failed`、`cancelled`、`timed_out`。

结果为 `kind: subworkflowFanoutResult`，带 group 身份、`groupRevision`、child workflow 身份、
固定策略、`status`、`businessOutcome`、`complete`、`error` 和按原输入顺序排列的 `items[]`。
`childPublishedVersion` 在类型中可选；平台必须从锁提供正确版本，不可从 saved 推断。
`counts` 包含 `total/successCount/failedCount/cancelledCount/timedOutCount/rejectedCount/pendingCount`，也展开在结果顶层；每项保留身份、输入、状态、result/error。
`complete` 只表示 child 均终态；businessOutcome 为 `empty/all_success/partial_failure/all_failed/incomplete`，到期封存可保留 pending 和清理责任。

## 5. 平台装配、恢复与兼容边界

首版 schema/runtime 均为每项一次尝试；恢复重接相同 child，不是重试业务副作用。
同一 parent/node 已封存时再次调用 collector 只返回原结果；重跑必须创建新执行，不能清空原账本。
不带 fanout/pinned 的既有单子流程保持旧契约，不新增发布锁要求。

OpenClaw `index.ts` 与 MCP `buildToolDeps` 已调用 `configureFanoutApiHost`，通过现有签名 API client 访问 ClawWeb。配置 `privateKeyB64` 时，client 以 Ed25519 签名 `${timestamp}.${JSON.stringify(body)}`（GET/DELETE 的 body 为空），发送 `X-Signature` 与 `X-Timestamp`；服务端校验五分钟时钟窗口和配置的公钥。缺签名/签名无效返回 401，非 loopback 的 unsigned dev 请求返回 403，服务端公钥未正确配置返回 503，错误形如 `{success:false,error,message}`。
执行授权 POST `/api/internal/bot-workflow-permissions/check`，请求体含 `botId`、`botOwnerId`、`workflowId`、`permission: execute`；只有成功响应且 `allowed === true` 才放行，缺少身份或读取失败均拒绝。
运行依赖 durable API：group GET/PUT `/api/internal/runs/:id/fanouts/:nodeId`，child GET/PUT `/api/internal/runs/:parent/fanout-children/:child` 与 PUT `/state`。接口错误使用 `{success:false,error,message}`：400 为输入/schema，404 为缺少 run，409 为 CAS/身份/生命周期冲突，500 为持久化失败，503 为数据库/容量配置不可用；client 不把读失败当作“没有状态”，只有 child GET 的明确 404 才表示尚无 child。
组合根已注入 `FanoutApiPersistence`、`DurableFanoutTaskFlow` 与独立 child executor factory；每个 child 使用自己的 session，不能复用父 session 执行闭包。`stateDirRef` 是逻辑身份；隔离目录和可信 initiator/runtime 上下文由宿主建立。
存储执行 revision CAS，并仲裁父取消/截止时间与成功封存；稳定 `childFlowId` 的 start-or-get 和 checkpoint 用于恢复重接，不能以进程内 map 替代 durable 状态。
本仓库当前没有 CCT workflow、脚本或真实业务 P5/P6 定义，本次没有迁移它们；集成 fixture 只验证引擎协议。上述接线不等于所有 Web 发布路径或任意平台已验收，也不承诺业务副作用 exactly-once。

## 6. 验证命令

在 `apps/evolverun/taskguard` 目录、依赖就绪后执行：

```bash
node --import tsx --test tests/subworkflow-fanout-validation.test.ts tests/workflow-dependencies.test.ts
node --import tsx --test tests/subworkflow-fanout.test.ts tests/fanout-persistence.test.ts tests/fanout-taskflow.test.ts
npm test
npx tsc --noEmit
npm run test:fanout-integration
```

最后一项单独运行 `integration-tests/subworkflow-fanout-e2e.test.ts`，不在默认 `npm test` 中。
它要求 ClawWeb 依赖已安装，且 ClawWeb 的 `public/shared` 与 `public/modules/workflow` 已完成 build。
真实 controller → HTTP → SQLite 集成已验证三项：4 成功 + 1 确认失败后进入 P6；
封存后父取消阻止 P6 副作用；封存 ACK 丢失后恢复复用 5 个 child、不重建。
这些测试不等于真实业务系统或所有平台的 E2E 验收。
