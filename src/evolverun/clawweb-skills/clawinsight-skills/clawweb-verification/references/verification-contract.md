# 验收协议 v1

## 读取接口

| 队列 | 路径 | 可检查状态 |
|---|---|---|
| 标准 | GET `/api/insight/v1/internal/governance/verification-candidates` | IN_PROGRESS且handledAt存在 |
| 开放 | GET `/api/insight/v1/internal/governance/verification-candidates/open` | ACTIVE或无handledAt的IN_PROGRESS |

使用队列契约已提供的身份、状态、版本、时间和根因字段。
单项详情接口可能需要额外Agent身份；缺少字段时保留证据不足，不借其他接口绕过权限。

## 判定门禁

- 标准验收确认消失前至少观察2天；开放验收至少7天。
- 复现必须是观察边界之后的同来源、同操作、同错误/根因。
- 确认消失需要完整观察覆盖；允许相关成功执行证明恢复，也允许观察期无复现（包括零新Session）。
- 新Session数量按独立Session去重，与估算任务数量、工具重试次数分开。
- 跨越修复边界的旧Session不能直接当作修复后全新的成功证据。
- 机器签名仅为可选定位信息；旧项按完整Owner+Bot范围检查，无错误可按观察期通过，有无法归属的错误则保留。

## 请求

标准结果POST到`/api/insight/v1/internal/governance/verification-results`；
开放结果POST到`/api/insight/v1/internal/governance/verification-results/open`。

```json
{
  "improvementId": 43,
  "version": 3,
  "outcome": "INSUFFICIENT_DATA",
  "newSessionCount": 0
}
```

示例ID和版本只说明格式，不能作为真实请求使用。
STILL_PRESENT必须补实际lastRecurrenceAt且至少有一个实际相关Session。
DISAPPEARED允许newSessionCount=0。标准接口此时必须显式发送allowZeroSession=true；
开放接口不接收该字段，直接按观察期校验。该字段不是强制关单，不绕过状态、观察期或证据检查。
程序不发送status、overrideActionType、审批或修复执行字段。
未精确对应原问题的Owner+Bot检查不虚报相关Session数；实际检查数量另记examined_sessions。

正式回写前重新读取队列，核对相同ID的当前状态与version。冲突或项目离开队列时停止并重新分析，
不盲目替换version重试。成功后核对ID、递增版本、状态和verificationStatus。
INSUFFICIENT_DATA只记录本地观察，不重复回写重置观察边界。

## 权限与非目标

服务地址、身份、只读NAS路径由独立受信任部署配置提供。
禁止读取或输出完整配置凭据。强制验收、已关闭项重开、自动修复降级、规则发布由独立授权流程处理。
生成请求、模拟POST、真实服务回执和业务问题消失是不同等级的证据，报告中分别说明。

## 日志读取契约

EvidenceStore.sources(owner, bot)枚举受信任NAS根下精确Owner+Bot的运行目录和Agent，
返回sources、complete、warnings；目录缺失、读取失败或超界不能视为空日志。
旧项与新项均需覆盖离线至当前的日志缺口。读取最近完整2/7天窗口，而非要求无限追溯项目全部历史。
仍须满足原handledAt/gmtModified观察起点；历史回放不允许正式写入。
