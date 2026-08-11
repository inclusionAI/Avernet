# bcsfuse fuse 接口经 Avernet gateway 对外开放 — 设计

- 日期:2026-08-04
- 状态:Draft(待评审;含 1 个阻断性开放问题与若干假定,见 §9)
- 目标仓库:Avernet(`/Users/wenyang/proj/alpharisk/Avernet`),分支 `bcsfuse_api`

## 1. 背景与目标

bcsfuse 当前作为独立 FastAPI 服务运行(port 8765,`src/bcsfuse/src/interfaces/api/app.py:60`)。前端(ocb 仓库 `src/frontend`)通过 `/bcnfuse` 代理前缀直连 `bcsfuse-pre/prod.alipay.com`,鉴权靠共享 Bearer token(`BCSFUSE_AUTH_TOKEN`)+ 转发用户 Cookie。

本次目标:把 bcsfuse 的 **融合 + 可融合查询** 接口接入 Avernet `src/gateway`(配置驱动转发面,提供统一鉴权与调用方身份),照已有 #712(BCN collaboration,commit `fc4de37b`)模板,使外部调用方经 gateway 统一信任边界访问 bcsfuse,而不是直连 bcsfuse。

**核心结论:gateway 侧基本不写代码**,主要是 config + OpenAPI 契约 + 测试;bcsfuse 侧不新增路由,只做"校验 principal + 解阻塞 + 契约对齐"等必要改动。

## 2. 关键事实(均已核实,file:line)

| 事实 | 依据 |
|---|---|
| gateway 是配置驱动转发面,无逐操作 handler;一个 HTTP catch-all + 每个 websocket domain 一个 WS 路由 | `src/gateway/src/gateway/community/adapters/web/app.py:37,138-148` |
| #712 模板:新增上游 domain 全靠 `application.yaml` + `configs/schemas/*.openapi.json` + `dump_and_publish.sh` + 测试,未新增 handler | commit `fc4de37b`;`configs/application.yaml` |
| gateway 按域名首段选 upstream,未匹配则拒绝(非开放代理) | `core/forwarding/_domains.py:1-21,294-303` |
| 鉴权后把调用方身份签成短命 JWT 注入 `X-Avernet-Principal`,入站该头被剥离防伪造 | `adapters/web/_forward.py:82,88,137-141` |
| gateway 仅剥离 `host` 与 `x-avernet-principal`,**Cookie 等其它头原样转发** | `_forward.py:88,141`(`_INBOUND_STRIP`) |
| 上游响应原样透传(status/headers/重复 Set-Cookie) | `_forward.py:159-171` |
| `rewrite.to: /` 是官方支持的"挂到上游根"剥前缀模式 | `core/forwarding/_domains.py:204-221,419-485`;`tests/test_domain_map.py:324-343` |
| "fuse" = 多智能体视角融合,不是文件系统 FUSE | `src/bcsfuse/src/application/services/group_fusion_service.py:237` |
| 融合主路由 `POST /groups/{group_id}/fuse`,挂在 `/api/v1` 下 | `app.py:106`;`fusion_parity_routes.py:163-198` |
| worker 配置(含 `fusion_enable`)路由挂在 `/v1` 下 | `app.py:104`;`worker_routes.py:1784,1807,1864` |
| "可融合"信号 = `worker.config.fusion_enable`;批量查询端点已存在 | `worker_routes.py:1864-1884`(`POST /workers/config/batch`) |
| G9(`bot_profile_fuse`)回调 BCN 取群上下文,靠转发用户 Cookie | `bot_fuse/group_context_service.py:83`;`infra/context/request_context.py:18`;`fusion_routes.py:370` |
| async 融合 handler 直接同步调 `service.fuse()`,阻塞事件循环 | `fusion_parity_routes.py:198`;`group_fusion_service.py:237` |
| bcsfuse 现鉴权 = 共享 Bearer token,不识别调用方身份 | `infra/public/auth/simple_token_auth_provider.py:18,35` |
| ⚠️ Avernet bcsfuse `FusionRequest` 为 `extra:"forbid"`,字段无 `session_id` | `src/bcsfuse/src/interfaces/api/schemas/fusion_schemas.py:148,151-204` |
| ✅ Avernet G9 取上下文用 `group_id`(BCN `/groups/{group_id}/messages`),全链路无 `session` | `bot_fuse/group_context_service.py:83-87,204`;`group_fusion_service.py:237,619,940` |
| ⚠️ ocb 前端 `postFuse` body 带 `session_id` | `ocb/src/frontend/src/services/backend-api/BcsfuseController.ts:13-31` |

## 3. 对外暴露面(D1)— 4 个已存在端点,无需新建路由

之前一度以为"查询可融合 bot"接口不存在、需新建;核实后**已存在**(批量查 `fusion_enable`)。bcsfuse 经 gateway 对外暴露的就是这 4 个端点:

| # | gateway 外部路径 | strip 后上游路径 | 上游前缀 | 用途 |
|---|---|---|---|---|
| 1 | `POST /openapi/v1/bcsfuse/api/v1/groups/{group_id}/fuse` | `/api/v1/groups/{group_id}/fuse` | `/api/v1` | 融合(前端用 G9 `bot_profile_fuse`) |
| 2 | `GET /openapi/v1/bcsfuse/v1/workers/{worker_id}/config` | `/v1/workers/{worker_id}/config` | `/v1` | 单查 `fusion_enable` |
| 3 | `PUT /openapi/v1/bcsfuse/v1/workers/{worker_id}/config` | `/v1/workers/{worker_id}/config` | `/v1` | 开关 `fusion_enable` |
| 4 | `POST /openapi/v1/bcsfuse/v1/workers/config/batch` | `/v1/workers/config/batch` | `/v1` | **批量查 `fusion_enable` = "可融合 bot 查询"** |

"可融合"判定 = `worker.config.fusion_enable === true`。ocb 前端 `fetchFusionBots`(`ocb/src/frontend/src/pages/GroupChat/hooks/useFuse.ts:90`)目前循环调端点 #2 而非 #4;后续可优化为 #4(非本设计必须)。

**不暴露**:workers/profiles/search/recommend/verify/admin(均为 bcsfuse 内部下游或仅供管理),其中 `recommend` 由 backend 服务端调用,非前端直连。

## 4. 架构与数据流

```
调用方 ──(Google/bot token)──> gateway /openapi/v1/bcsfuse/...
   │  gateway: route_security user:required → 解析调用方身份
   │            → 签 X-Avernet-Principal(audience=bcsfuse)注入
   │            → Cookie 等头原样转发;路径 strip rewrite(/ → 剥 domain 前缀)
   ▼
bcsfuse /api/v1/... 或 /v1/...
   │  bcsfuse: 校验 X-Avernet-Principal(D2 假定)→ 拿 tenant/user
   │            service.fuse() 包 run_in_threadpool(不阻塞事件循环)
   │            G9: 用转发来的 Cookie 调 BCN 取群上下文(D2 假定)
   ▼
(响应原样经 gateway 透传回调用方)
```

- **鉴权(D2,从轻/已定)**:gateway `route_security: {user: required}` 挡匿名;bcsfuse **不校验 `X-Avernet-Principal`**,靠"gateway 放行即信任";加 trust-gateway 开关跳过老的共享 Bearer 校验。bcsfuse 不识别调用方、无按身份审计(从轻取舍)。
- **G9 身份传播(D2,已定)**:gateway 转发用户 Cookie,bcsfuse G9 继续用 cookie 转发调 BCN;前提 BCN cookie 域覆盖 gateway 域(部署 checklist)。
- **长耗时(D3,已定)**:一期用长超时 HTTP;gateway 流式转发已支持。前端现用 `timeout_ms=180000`;gateway/上游超时调到 ≥600s 留余量(与 bcsfuse `MAX_TIMEOUT_MS` 一致)。WS 流式列为后续。

## 5. 改动清单

### 5.1 gateway 侧(纯配置 + 契约 + 测试,照 #712)

`configs/application.yaml`(`user_config` 下):
```yaml
upstream_vars:
  bcsfuse_server_url: https://bcsfuse.sample.com   # 必须带 scheme
servers:
  bcsfuse:
    base_url: "${bcsfuse_server_url}"
domains:
  bcsfuse:
    server: bcsfuse
    protocols: [http]            # bcsfuse 无 WS
    rewrite:
      from: /openapi/v1/bcsfuse
      to: /                      # 剥 domain 前缀,保留上游 /api/v1 与 /v1
    schema:
      source: file
      path: schemas/bcsfuse.openapi.json
      refresh_seconds: 300
route_security:
  "/openapi/v1/bcsfuse/**":
    user: required
```

- `configs/schemas/bcsfuse.openapi.json`:发布 4 条路径(§3 表中的外部路径),`x-avernet-security` 由 gateway 在 serve 时按 `route_security` 盖章(`_openapi.py`),源契约可不带。更新 `configs/schemas/README.md`。
- `scripts/dump_and_publish.sh`:注册 `bcsfuse` 到 `_upstream_dir`/`_upstream_env`,加 dump/gate 块。**需给 bcsfuse 补 `scripts/dump_openapi.py`**(调 `app.openapi()` 写 JSON;BCS 有同名脚本可参照),或直接提交静态 artifact。过 backward-compat 闸(`core/forwarding/_compat.py`)。
- 测试(参照 `tests/test_domain_map.py`、`tests/test_route_security.py`、`tests/test_served_openapi.py`、`tests/integration/test_live_bcs_forwarding.py`):
  - `test_domain_map`:shipped config 把 `/openapi/v1/bcsfuse/api/v1/groups/{id}/fuse` strip 到 `/api/v1/...`、`/v1/workers/config/batch` strip 到 `/v1/...`;`serves_http=True`、`serves_websocket=False`。
  - `test_route_security`:`/openapi/v1/bcsfuse/**` → user required。
  - `test_served_openapi`:4 路径在 namespace 内且 security 盖章正确。
  - 可选 `tests/integration/test_live_bcsfuse_forwarding.py`(env 门控),仿 `test_live_bcs_forwarding.py`。

### 5.2 bcsfuse 侧

1. **trust-gateway 开关(D2,从轻)**:被 gateway 前置时跳过 `require_oss_auth`/共享 Bearer 校验(如 `BCSFUSE_TRUST_GATEWAY=1`),不校验 `X-Avernet-Principal`、不要求共享 Bearer。bcsfuse 不识别调用方身份(从轻取舍,放弃 `FuseMetadata.operator` 按身份写入与审计)。
2. **解事件循环阻塞**:把 `service.fuse()`(`fusion_parity_routes.py:198`)包进 `starlette.concurrency.run_in_threadpool`,使 gateway 并发不串行。
3. **契约对齐(Q1,已定 A/从轻)**:`FusionRequest` 新增 `session_id: Optional[str] = None`(接收但**不使用**,G9 仍按 path `group_id` 取上下文);保持 `extra:"forbid"` 对其它未知字段仍严格。前端无需改。
4. (可选)响应信封对齐 `{code,message,data,request_id}`,使跨 domain 一致(gateway 不归一化);非必须。

### 5.3 前端侧(若调用方为 ocb 前端)

`ocb/src/frontend/src/services/backend-api/BcsfuseController.ts`:把 base 由 `/bcnfuse` 改为 `/openapi/v1/bcsfuse`,**路径后缀不变**(`strip rewrite` 保证上游 path 一致)。配合 cookie 跨域发送(withCredentials / cookie 域)。

## 6. 错误处理

- bcsfuse 的 `HTTPException`(400/401/404/422/500/503)原样经 gateway 透传(status+body 不变)。
- gateway 仅对自身故障合成信封 `{code, message, data:null, request_id}`:未知/非 http domain → 404;鉴权失败 → 401;principal 签名失败 → 500;上游不可达 → 502。
- bcsfuse 响应信封与其不同;若不执行 §5.2-5,外部客户端会看到 bcsfuse 原样信封(跨 domain 不一致,可接受)。

## 7. 测试计划

- gateway:§5.1 所列单元/集成测试;`just test` 跑全;`just test-arch` 确保未违反六边形层规则(纯配置改动应不触发)。
- bcsfuse:为 principal 校验加单测;为 `run_in_threadpool` 包裹加并发回归(多请求不串行);`session_id` 契约对齐后补契约测试。
- 契约:`scripts/gate_and_publish_openapi.py` 防 breaking 变更。
- 可选 live:env 门控的真连 bcsfuse 转发测试。

## 8. 决策记录

| # | 决策 | 选择 | 状态 |
|---|---|---|---|
| D1 | 范围 | 4 个已存在端点(fusion + 3 worker-config 含批量可融合查询),无新建路由 | ✅ |
| D2 | 鉴权/身份 | 从轻:bcsfuse 不校验 principal、仅信任 gateway + G9 cookie 转发 | ✅ 用户已确认(从轻) |
| D3 | 长耗时 | 长超时 HTTP(≥600s 余量),WS 流式列后续 | ✅ 用户已确认 |
| D4 | 路径前缀 | 一个 `bcsfuse` domain + strip rewrite `to:/`,不改 bcsfuse 路由;`to:/` 已确认被 gateway 支持 | ✅ |
| D5/D6 | fusable 口径/新建 | 取消(端点已存在,口径=`fusion_enable`) | ✅ |
| 目标 bcsfuse | Avernet `src/bcsfuse` | — | ✅ 用户已确认 |
| Q1 | session_id 处置 | Avernet 容忍 `session_id`(接收不用),前端不改(G9 仍用 group_id) | ✅ 用户已确认(A/从轻) |

## 9. 开放问题与假定(评审重点)

**Q1(已定 A/从轻)契约不对齐:调用方与 `session_id`。** 已核实:Avernet G9 取上下文用 `group_id`(BCN `/groups/{group_id}/messages`),全链路无 `session`(`group_context_service.py:83-87,204`;`group_fusion_service.py:237,619,940`);`FusionRequest` 为 `extra:"forbid"`、无 `session_id`(`fusion_schemas.py:148,151`)。ocb 前端 `postFuse` body 带 `session_id`(`BcsfuseController.ts:13-31`)——该字段 Avernet 既不接受(422)也不使用(ocb 版按 session 隔离上下文,Avernet 按 path 的 group_id)。需用户裁定调用方与处置:
- (a) ocb 前端靶向 Avernet 时**去掉 `session_id`**(上下文语义从 session 维度退化为 group 维度);
- (b) Avernet `FusionRequest` 放宽容忍 `session_id`(接收但不使用,仅防 422);
- (c) 实际调用方并非 ocb 前端(请明确,如 BCS Rust `bcs-fuse-client`)。

→ **已定:(b) Avernet 容忍 `session_id`**(新增可选字段、接收不用,前端不改,G9 仍用 group_id)——从轻。

**Q2(D2)** ✅ 已定(从轻):bcsfuse 不校验 principal、仅信任 gateway(`user:required` 挡匿名)+ G9 保留 cookie 转发。

**Q3** 实际外部调用方是谁(ocb 前端 / BCS `bcs-fuse-client` / 其它)?决定 §5.3 前端改动是否在本期范围。

**Q4(D2 从轻→moot)** 从轻方案不校验 principal,无需 verify-side 密钥配置。

**Q5** BCN cookie 域是否覆盖 gateway 域(决定 G9 cookie 转发是否可直接用,还是要走服务身份)。

**Q6** 移除 `BCSFUSE_AUTH_TOKEN` 共享 Bearer 是否影响其它现存直连 bcsfuse 的调用方(如单测、`bcs-cli`、singlebox 编排)。

## 10. 非目标 / 后续

- fusion 流式(WS 逐 perspective 推送 + 最终 fusion 事件,仿 BCN collaboration)。
- G9 改服务身份调 BCN(摆脱 cookie 域依赖)。
- 跨 domain 响应信封统一。
- ocb 前端 `fetchFusionBots` 改用批量端点 #4 替代 N 次单查。
- 暴露更多 bcsfuse 路由(若有需求)。
