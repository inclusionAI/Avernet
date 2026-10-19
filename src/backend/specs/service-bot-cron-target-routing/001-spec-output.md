# 服务 Bot 定时任务：创建时确定环境、实例和 Caller

日期：2026-10-11。状态：需求与核心改动方案，尚未编码、部署或运行验证。

## 基线与目标

- OCB 远端 dev：42ace7ecebc8d9e6490f34d5818bb2b518288fc9。
- OCB 对应 Avernet gitlink：4be93ad5dcb5edee79baf8421b029ce20eee9267。以下 backend community 路径均属于 Avernet，不能用 OCB 当前本地 checkout 代替。
- daas-scripts 远端 dev：20dede548eb34613d363bf8fc8b78d337f19d050。
- 用户确认：operator_id 是任务创建人，同时作为 Caller 身份。
- 成功标准：一条创建请求只写入一个明确的运行容器；发布、扩容、重发不把任务带到其他容器；查询和管理可再次定位该容器。

## 当前源码证据

| 仓库路径 / 位置 | 当前行为 |
| --- | --- |
| Avernet `apps/backend/src/agentclaw/community/adapters/http/cron/router.py:280`，`create_cron` | 手工读取请求 JSON，320 行调用 service；没有传入 stage、实例或创建人。 |
| Avernet `apps/backend/src/agentclaw/community/core/cron/services/cron_runtime_operations.py:345`，`create_cron` | 固定 `runtime_stage=RUNTIME_STAGE_DRAFT`。 |
| 同文件 `:262`、`:385`、`:425` | 存在多实例广播；发布态 update 只允许 enabled 并广播，delete 拒绝发布态。新增创建能力必须配套单实例管理。 |
| Avernet `apps/backend/src/agentclaw/community/core/cron/services/cron_runtime_targets.py:39` | 已有 CronRuntimeTarget，字段为 binding_id、runtime_stage、device_uuid；不必重新设计通用传输层。 |
| Avernet `apps/backend/src/agentclaw/community/core/runtime_binding/service.py:52` | 已有 RuntimeBindingResolutionService.resolve，可以解析草稿、预发、生产或 Caller binding。 |
| 同文件 `:150`、`:174` | Caller 使用 actor_user_id、bot_id、owner_id 查实例；校验 binding 的环境、主体、申请原因、创建人和 provider。失败不能回退父 Bot。 |
| Avernet `apps/backend/src/agentclaw/community/core/workspace/engines/openclaw.py:38` | 仅排除 cron/runs/，没有排除任务目录。Claude Code 也只排除 runs；AICoding build plan 额外包含 sessions/cron-tasks.json。 |
| Avernet `apps/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py:1296` | 构建 rsync 使用 --delete-excluded，能够清理复用制品中的已排除状态；`:1786` 草稿恢复使用 --delete，排除规则也必须保护草稿任务。 |
| daas-scripts `bootstrapping/service_bot_transition.sh:460`、`:484`、`:782` | cp 和 rsync 两条复制路径；需要两者都跳过任务状态。 |
| 同文件 `:850` | 仅 eval + openclaw 删除 cron 内容。不能把这一删除逻辑直接扩展到预发/生产，会删除目标实例独立创建的任务。 |

## 契约与路由

继续使用 POST /api/cron，认证沿用现有入口。认证参数不进入示例、日志或报告。

建议复用既有 runtime_stage，取值 draft / verify / online；这分别对应服务 Bot 草稿、预发、正式运行态。它与 backend 部署环境 SERVER_ENV 不同，不能仅因入口是生产域名就选 online，也不能以请求字段切换后台数据库环境。

```json
{
  "bot_id": "service_bot_id",
  "runtime_stage": "online",
  "device_id": "runtime_device_uuid",
  "operator_id": "caller_staff_id",
  "name": "每日汇总",
  "schedule": "0 9 * * *",
  "command": "汇总昨日数据",
  "timezone": "Asia/Shanghai",
  "enabled": true
}
```

device_id 的 API 含义明确为运行实例 UUID，对接内部 device_uuid；不是 binding_id、BaaS Bot UUID、设备表自增 ID 或 ARCA shell ID。如果调用方目前传的是其他 ID，需要在既有设备服务内明确转换，不能猜测。内部已有 device_uuid 查询契约时统一名称或作为兼容别名；同时提供且不一致应拒绝。

创建流程：真实登录用户权限校验 → 校验 operator_id → 选择服务/Caller binding → 校验 device_id 属于该 binding 和运行态 → 固定单个 CronRuntimeTarget → 原传输接口调用一次。

- 常规服务 Bot：通过既有发布记录选择 draft/verify/online binding，再验证并锁定一个 device_uuid。多实例创建缺少 device_id 时拒绝，不能随机选或广播。
- Caller 服务 Bot：operator_id 作为 actor_user_id，选择该 Caller 已有实例 binding；不自动创建或唤醒 Caller 容器。device_id 必须属于该 Caller，不能拿父服务实例替代。
- operator_id 不替换真实登录用户 user.staffId，也不替换 Bot owner_id。本人创建可默认当前登录用户；代建需通过现有 Bot 管理授权，没有代建权限则拒绝。日志同时记录真实操作者和创建人。
- 显式给出错误 stage、Caller、device_id 或不可用 binding 时明确失败，不回退到草稿或其他容器。
- 个人 Bot 和未使用新参数的旧请求保留既有草稿路径；服务 Bot 的预发/生产创建必须明确目标。前端配套提交选择的 stage 和实例。
- RuntimeBindingResolutionService 的 Caller 分支目前按 Caller 当前实例查找，并不自动建立“每个 stage 一个 Caller 实例”的映射。必须明确所选 Caller 实例对应哪个发布运行态；若既有元数据不能证明，则拒绝不匹配请求，不声称 stage 已验证。

## 核心代码改动（示意，非已提交补丁）

### 1. HTTP adapter 只解析和传参

在既有 cron/router.py 中修改原 create_cron endpoint，不新建子 router。

```python
result = await service.create_cron(
    bot_id=bot_id,
    user_id=effective_user_id,  # 保留原 Bot 权限/Owner 上下文
    nick_name=effective_nick_name,
    body=adapter_body,
    runtime_stage=data.get("runtime_stage", "draft"),
    device_id=data.get("device_id"),
    operator_id=data.get("operator_id") or user.staffId,
    authenticated_user_id=user.staffId,
)
```

router 不查询发布记录、不选择 binding、不按设备循环。路由字段不盲目塞入底层引擎任务 body；底层未知字段可能被拒绝。operator_id 首先作为路由和审计字段，若要求任务详情永久保存创建人，需单独核验引擎已有 metadata 契约再最小接线。

同目录 schemas.py 的 CreateCronRequest 当前未被该入口使用。新增字段时要实际校验原始 JSON，不能只改未接入的模型。

### 2. Cron 领域服务解析并固定目标

在 cron_runtime_operations.py/cron_runtime_targets.py 的既有领域 service 内增加参数与单目标解析；DI 注入已有 RuntimeBindingResolutionService。下面省略已有权限、异常映射和设备状态校验，只展示关键接线。

```python
resolved = self._runtime_binding_service.resolve(
    RuntimeBindingRequest(
        bot_id=bot_id,
        owner_id=authorized_owner_id,
        actor_user_id=operator_id,
        stage=runtime_stage,
        target=RuntimeBindingTarget.AUTO,
    )
)
# 使用既有设备 provider 验证实例属于 resolved.binding_id。
# 将验证过的 device_id 映射成 CronRuntimeTarget.device_uuid。
target = replace(base_target, binding_id=resolved.binding_id,
                 device_uuid=validated_device_uuid)
return await self._forward_runtime_target_request(
    target, method="POST", path="/api/cron", body=body,
)
```

base_target 必须使用本次 resolved binding 构造，不能预先走旧草稿解析：Caller 父 Bot 可能没有草稿 binding。AUTO 在服务 Bot call_type=caller 时选择 Caller；如果未来要在同一 Caller 服务 Bot 上区分父服务任务和 Caller 任务，应显式定义 scope，不能用 operator_id 是否等于 owner 来猜。

cron_runtime_targets.py:228 的 _resolve_runtime_context 也必须沿用锁定 binding。Caller 即使 stage=draft，仍调用既有 device_context_resolver.resolve_for_binding(binding_id, authenticated_user_id, bot_id=bot_id, device_uuid=...)，不能再走父 Bot 的默认草稿 resolver。device_context_resolver.py:106 已提供此接口。

创建响应保留原任务结果，并附 runtime_stage、device_id、operator_id，供后续管理定位。不扩张数据库 schema，不重写 Engine/Relay 调度器。

### 3. 单实例管理

detail/update/delete/run/runs/running 使用相同目标字段和领域解析。显式 device_id 时只操作该容器；删除解除发布态禁止的前提是已验证单个目标。已存在的聚合列表/批量启停兼容路径保留，但不得套用到新任务的单实例操作。前端保存创建返回的目标字段并用于后续请求；列表行携带实例信息。

### 4. 发布不携带任务，目标任务保持原样

- OpenClaw provider：将仅排除 cron/runs/ 改为排除整个根目录 /cron/，包含 jobs.json、备份及运行记录。
- 其他引擎必须按真实任务存储位置定义规则；已确认 AICoding extra_include_files 包含 sessions/cron-tasks.json，支持该引擎时必须取消这条额外复制，并排除相应任务/运行状态。Relay 实际目录需独立核验，不能从旧兼容代码推断当前部署。
- 构建排除用于生成新制品，原 --delete-excluded 清理复用制品中的任务残留。只在制品目录执行清理，不能复制到运行目录后再删除。
- daas install_dir_contents：cp 的 items 选择与 rsync 的 exclusions 均加入引擎根目录任务排除。即使传入旧版本制品，也不能复制其中 Cron。保留原 no-delete 目标目录合并语义。
- 草稿恢复使用同一任务排除，保护草稿已有任务；避免 --delete-excluded 进入草稿/预发/生产运行目录。
- 发布/扩容后新容器默认无业务任务；原容器已有任务保持。旧重复任务需另做盘点并由用户选定保留实例，本次不自动批量清理。

## 允许与禁止改动边界

允许：Avernet cron HTTP adapter、Cron 领域服务/最小 DI、相关前端字段透传、服务发布 build plan、daas 安装时任务排除，以及对应测试。

复用：runtime_binding、发布记录解析、设备 provider、device_uuid 传输。

禁止：在 router 编排 Caller/设备；创建通用路由框架；改通用 HTTP 客户端或无关数据库 schema；重写 Relay/OpenClaw 调度器；把发布期间 rm cron 当成排除；任务在容器消失后自动漂移到其他实例。

## Review 与验证计划

1. 本人/获授权代建/无权代建；Caller A 的 device_id 不可创建到 Caller B 或父服务；不存在/不可用 Caller 不回退。
2. draft/verify/online 三个已存在 binding，各只调用一次目标 transport；多实例 A/B 中仅 A 获得新任务；错误设备及缺实例拒绝。
3. create 返回目标字段，detail/update/delete/run 使用同一字段只命中原实例；没有无意广播或修改既有聚合查询语义。
4. 草稿有任务、预发和生产各有独立任务：发布后制品无任务，目标原任务内容不变；新扩容实例无任务；旧含任务制品的 cp/rsync 安装都不会导入任务。
5. 同版本制品重建清理旧任务残留；草稿恢复保留草稿任务；个人 Bot 行为和既有 eval 清理规则回归。
6. 新增 cron.create.request / cron.target.resolved / cron.transport.request / cron.transport.success / cron.transport.failure 事件（最终名称依既有日志约定确定）。记录 bot、stage、device、Caller、真实操作者、binding、方法/路径、业务任务参数、结果、耗时和错误；认证 token 不输出。测试断言事件、业务字段、敏感值不落日志。
7. 编码后执行独立 Review + 本地回归，最多三轮修复；不能把 mock 路由测试称为真实发布/定时执行验证。

## Ship 与限制

本轮仅交付方案。spec 确认后进入编码；发布前按 tc-main-agent 需要用户确认代码。Avernet 源码通过 inclusionAI/Avernet 交付，镜像 SHA 可验证后再更新 OCB gitlink；daas-scripts 独立交付。未部署、未创建线上任务、未启动 OpenClaw，因此以上事实是固定提交源码行为，不是线上黑盒结论。

本方案消除跨阶段、跨实例的任务复制；不保证网络重试创建的幂等性，也不保证容器内执行 exactly-once。固定容器任务不随销毁/替换自动迁移；如需容灾迁移须另定义持久化与调度语义。

## 2026-10-11 编码补充
用户已批准编码、rebase到dev、创建PR和修复CI，且确认新建隔离worktree。Avernet编码基线使用GitHub dev 193256c75，OCB 42ace7eceb，daas 20dede54。旧无目标参数请求保留草稿父服务；显式Caller定向创建只接受online，verify/draft不伪装为Caller隔离环境。device_id映射内部device_uuid，指定实例管理走同一解析。前端位于OCB corp，独立PR；源码先GitHub，镜像可验证后再更新gitlink。

## 2026-10-19 字段更名

按用户要求，本文接口字段已统一为operator_id，替代原create_by_id；它仍表示任务创建人/Caller身份。真实登录操作者在内部单独使用authenticated_user_id，不能由请求字段覆盖。详见012-operator-id-rename.md。
