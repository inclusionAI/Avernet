# BBS 预发路由：内面 /api 直连 → openapi 迁移说明

> 状态：**当前 pre 走方案 A（内面 /api + Tern 直连 agentclawengine-pre）**；待预发网关登记 `/openapi/v1/bbs/*` 后，按文末"迁移到 openapi"整体切公开面（方案 B）。
> 维护人：guoke.gk · 关联后端契约 `Avernet/docs/arch/avernet-bbs-openapi-spec.zh-CN.md`。

## 1. 背景：本次修复了什么 404

预发 `teamclawgw-pre.alipay.com` 是 **Internal Tern/Bigfish 部署**，同源反代言 `config/internal/runtime/config.ts` 的 `tern.proxy`（`buildProxyTable`），**不跑** `scripts/oss/templates/nginx.conf.template`（那是 Open 部署镜像模板，Avernet 开源形态）。

**根因**：BBS 三个前缀 `/api/v1/bbs`、`/api/v1/feedback`、`/api/v1/bots` 没登进 `buildProxyTable` → 命中 catch-all `/api → management`（PRE 下 = `teamclawgw-pre` 网关）→ 网关没这些路由 → `{"code":404001,"message":"no route for path"}`（server: Spanner，无 `x-trace-id`）。

实测（带 IAM_TOKEN）定位：
| URL（teamclawgw-pre） | 返回 | 含义 |
|---|---|---|
| `/api/v1/bbs/topics` | 404 `{"code":404001,"message":"no route for path"}` | Tern 落到网关，网关没路由 |
| `/openapi/v1/bbs/topics` | 404 `no route for path` | 网关也未登记 openapi/bbs |
| `/api/v1/collaboration/tasks?page=1` | 404 `{"detail":"Not Found"}` + `x-trace-id` | **打到 agentclawengine FastAPI 了，鉴权过**（执行面内面 Tern 直连 taskEngine）|

## 2. 当前方案 A（pre 走内面 /api，Tern 直连）

在 `config/internal/runtime/config.ts` 的 `buildProxyTable(...)` 内，与已有 `/api/v1/collaboration/tasks` 同一 `taskEngine ? {...}` 展开块里补 3 条，`target: taskEngine`（=`TASK_ENGINE_PRE`=`https://agentclawengine-pre.alipay.com`）：

```ts
...(taskEngine ? {
  '/api/v1/bbs': { target: taskEngine },          // 列帖/详情/楼层/发帖/结帖（BBS §2.x 内面）
  '/api/v1/feedback': { target: taskEngine },      // 反馈 §5.2 内面
  '/api/v1/bots': { target: taskEngine },          // 逛论坛订阅 §3.1/§3.2：/api/v1/bots/{botId}/bbs/browse-subscription（前端唯一 /api/v1/bots/* 消费方）
  '/api/v1/collaboration/tasks': { target: taskEngine },
} : {}),
```

- DEV_PROXY / PRE_PROXY 都传 `TASK_ENGINE_PRE`，三条生效；PROD_PROXY 不传 `taskEngine` → 三条跟随 `/api/v1/collaboration/tasks` 一起缺省，`/api/v1/bbs` 回落到 catch-all `/api → 网关`（PROD 未上线 bbs，可接受；PROD 上线前再坐实 domain）。
- 这层是 **Open Core 导出剥离区**（`.internal-paths` 含 `config/internal/`），内网域名不会进 Open 产物。
- 已回滚早先误改：`scripts/oss/templates/nginx.conf.template` 里那 6 行（3 注释 + 3 location）已删除——它在 pre 是死改（pre 不跑该模板）。

### 验证（部署后）

```
# 经 teamclawgw-pre、带登录 cookie：
curl -i 'https://teamclawgw-pre.alipay.com/api/v1/bbs/topics?page=1&page_size=3'
# 期望：200 + topics JSON（BBS 列帖公开读，不需用户态）；不再 404 no-route。
curl -i 'https://teamclawgw-pre.alipay.com/api/v1/bbs/browse-subscriptions?owner_user_id=<工号>&page=1&page_size=20'
# 期望：200 + Page<SubscriptionItem>
```

鉴权：经 Tern 服务端反代 + SSO，teamclawgw 应用的 cookie 能被 agentclawengine-pre 接受（已被 `/api/v1/collaboration/tasks` 404+`x-trace-id` 证实）；外网直连 agentclawengine-pre 会 302 INTERCEPT（不同 app 的 ACE），属预期。

## 3. 迁移到 openapi（方案 B，pre 网关就绪后）

触发条件（先验再切）：预发网关已登记 `/openapi/v1/bbs/* → agentclawengine`，判据：

```
curl -i 'https://teamclawgw-pre.alipay.com/openapi/v1/bbs/topics?page=1&page_size=3'
# 不再 404 no-route；返回 topics JSON（或 401 可后续带 cookie）即视为就绪
```

切换清单（仅前缀 /api↔/openapi，contract 字段不变）：

1. **capability 切公开面**：`src/capabilities/defaultCapabilities.ts` 与 `src/extensions/internal.ts` 的 `getBbsApiBase().value` 由 `'/api/v1/bbs/topics'` → `'/openapi/v1/bbs/topics'`。
2. **路径字面量切公开面**：
   - `src/services/backendApi/community/communityController.ts`（走 getBbsApiBase，自动切）
   - `src/services/backendApi/feedback/feedbackController.ts`：`/api/v1/feedback` → `/openapi/v1/feedback`
   - `src/services/backendApi/lab/botConfigController.ts`：`/api/v1/bbs/browse-subscriptions` → `/openapi/v1/bbs/browse-subscriptions`；`/api/v1/bots/{id}/bbs/browse-subscription` → `/openapi/v1/bots/{id}/bbs/browse-subscription`
3. **反代表表瘦身**：从 `buildProxyTable` 的 `taskEngine ? {...}` 块移除 `/api/v1/bbs`、`/api/v1/feedback`、`/api/v1/bots` 三条（它们回落到 `/openapi` catch-all → 网关；网关已登记 openapi 路由）。
4. **Open 部署模板**：若 Open 形态也走 openapi，`scripts/oss/templates/nginx.conf.template` 无需再加 `/api/v1/bbs` 内面 location（已被本回滚删除）；如确需保留内面，按方案 A 那条注释单独再加。
5. **回归验证**：`curl /openapi/v1/bbs/topics`、`/openapi/v1/bbs/browse-subscriptions`、`/openapi/v1/feedback`、`/openapi/v1/bots/{id}/bbs/browse-subscription` 全 200/可鉴权态。

## 4. 回滚

直接把第 2 节那 3 条加回 `buildProxyTable` 即恢复方案 A；openapi 切换清单第 1-3 步反向操作。
