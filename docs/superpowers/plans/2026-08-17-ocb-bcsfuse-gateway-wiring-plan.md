# ocb 内部版 bcsfuse Gateway 接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ocb 内部 gateway 中补全 bcsfuse 的 upstream、domain、schema 和 route_security 配置，使外部流量可通过 `/openapi/v1/bcsfuse/*` 转发到内部 bcsfuse 服务。

**Architecture:** 完全参考 Avernet 开源 gateway 的 bcsfuse 配置：新增 `bcsfuse` server，配置 `bcsfuse-fusion` 与 `bcsfuse-workers` 两个 domain，复制 Avernet 的 schema 文件到 ocb gateway schemas 目录，并声明 `/openapi/v1/bcsfuse/**` 需要 `user` 身份。

**Tech Stack:** YAML, ocb gateway (teamclawgw), internal bcsfuse (FastAPI)

## Global Constraints

- 不修改内部 bcsfuse 业务代码；只在其真实路由与 Avernet schema 不匹配时调整 ocb schema 或补充 parity route。
- 所有 env overlay 必须同时新增 `bcsfuse_server_url`。
- `bcsfuse` upstream server base_url 必须来自变量 `${bcsfuse_server_url}`，不得硬编码。
- `/openapi/v1/bcsfuse/**` route_security 规则保持 `user: required`。

## 新增/修改文件清单

| 角色 | 路径 |
|------|------|
| 创建 | `ocb/src/gateway/configs/schemas/bcsfuse-fusion.openapi.json` |
| 创建 | `ocb/src/gateway/configs/schemas/bcsfuse-workers.openapi.json` |
| 修改 | `ocb/src/gateway/configs/application.yaml` |
| 修改 | `ocb/src/gateway/configs/application-dev.yaml`（及各 env overlay） |
| 新增/修改 | `ocb/src/gateway/tests/unit/core/forwarding/test_domain_map.py` |

---

### Task 1: 核对内部 bcsfuse 实际路由

**Files:**
- Read: `bcsfuse/src/interfaces/api/fusion_routes.py`
- Read: `bcsfuse/src/interfaces/api/worker_routes.py`
- Read: `bcsfuse/schemas/openapi.yaml`

**Interfaces:**
- Produces: 明确三条 gateway 路径是否可映射到内部 bcsfuse。

- [ ] **Step 1: 检查 fusion endpoint**

Run:
```bash
grep -n "fuse" /Users/wenyang/proj/alpharisk/bcsfuse/src/interfaces/api/fusion_routes.py
```

Expected: 存在类似 `POST /api/v1/groups/{group_id}/fuse` 的路由。

- [ ] **Step 2: 检查 worker config endpoint**

Run:
```bash
grep -n "config" /Users/wenyang/proj/alpharisk/bcsfuse/src/interfaces/api/worker_routes.py
```

Expected: 存在 `GET /v1/workers/{worker_id}/config` 与 `PUT /v1/workers/{worker_id}/config`。

- [ ] **Step 3: 检查 batch config endpoint**

Run:
```bash
grep -n "batch" /Users/wenyang/proj/alpharisk/bcsfuse/src/interfaces/api/worker_routes.py
```

Expected:
- 若存在 `/v1/workers/config/batch` 或 `/v1/admin/workers/config/batch`，则 gateway schema 可保留 batch 路径。
- 若不存在，则在后续 Task 4 中从 ocb schema 删除该路径。

- [ ] **Step 4: 记录核对结论**

在 ocb 仿照 Avernet 的表格填写结论：

| Gateway 路径 | Upstream path | 内部是否支持 | 备注 |
|---|---|---|---|
| `/openapi/v1/bcsfuse/groups/{group_id}/fuse` | `/api/v1/groups/{group_id}/fuse` | ？ | |
| `/openapi/v1/bcsfuse/workers/{worker_id}/config` | `/v1/workers/{worker_id}/config` | ？ | |
| `/openapi/v1/bcsfuse/workers/config/batch` | `/v1/workers/config/batch` | ？ | 不支持则删除 |

- [ ] **Step 5: Commit 核对结果**

```bash
git commit -am "docs: verify internal bcsfuse upstream routes for gateway wiring

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 复制 / 调整 bcsfuse schema 文件

**Files:**
- Create: `ocb/src/gateway/configs/schemas/bcsfuse-fusion.openapi.json`
- Create: `ocb/src/gateway/configs/schemas/bcsfuse-workers.openapi.json`

**Interfaces:**
- Consumes: Avernet gateway schema files。
- Produces: ocb gateway schema files，如有不匹配则按 Task 1 结论调整。

- [ ] **Step 1: 从 Avernet 拷贝**

```bash
cp /Users/wenyang/proj/alpharisk/Avernet/src/gateway/configs/schemas/bcsfuse-fusion.openapi.json \
   /Users/wenyang/proj/alpharisk/ocb/src/gateway/configs/schemas/
cp /Users/wenyang/proj/alpharisk/Avernet/src/gateway/configs/schemas/bcsfuse-workers.openapi.json \
   /Users/wenyang/proj/alpharisk/ocb/src/gateway/configs/schemas/
```

- [ ] **Step 2: 如需调整 batch path**

若 Task 1 确认内部 bcsfuse 不支持 `/v1/workers/config/batch`，则编辑 `bcsfuse-workers.openapi.json` 删除 `/openapi/v1/bcsfuse/workers/config/batch` 路径。

- [ ] **Step 3: Commit**

```bash
cd /Users/wenyang/proj/alpharisk/ocb
git add src/gateway/configs/schemas/bcsfuse-*.openapi.json
git commit -m "feat(gateway): add bcsfuse openapi schemas

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 更新 ocb gateway `application.yaml`

**Files:**
- Modify: `ocb/src/gateway/configs/application.yaml`

**Interfaces:**
- Produces: bcsfuse server + two domains + route_security。

- [ ] **Step 1: 在 servers 段添加 bcsfuse**

```yaml
  servers:
    backend:
      base_url: "${backend_server_url}"
    bcsfuse:
      base_url: "${bcsfuse_server_url}"
```

- [ ] **Step 2: 在 domains 段添加 bcsfuse-fusion / bcsfuse-workers**

```yaml
    bcsfuse-fusion:
      match: /openapi/v1/bcsfuse/groups/**
      server: bcsfuse
      protocols: [http]
      rewrite:
        from: /openapi/v1/bcsfuse/groups
        to: /api/v1/groups
      schema:
        source: file
        path: schemas/bcsfuse-fusion.openapi.json
        refresh_seconds: 300

    bcsfuse-workers:
      match: /openapi/v1/bcsfuse/workers/**
      server: bcsfuse
      protocols: [http]
      rewrite:
        from: /openapi/v1/bcsfuse/workers
        to: /v1/workers
      schema:
        source: file
        path: schemas/bcsfuse-workers.openapi.json
        refresh_seconds: 300
```

- [ ] **Step 3: 在 route_security 段添加 bcsfuse**

```yaml
route_security:
  "/openapi/v1/bots/**":
    user: required
  "/openapi/v1/bcsfuse/**":
    user: required
```

- [ ] **Step 4: 在每个 env overlay 添加 `bcsfuse_server_url`**

编辑 `ocb/src/gateway/configs/application-dev.yaml`、`application-gray.yaml`、`application-prepub.yaml`、`application-prod.yaml`、`application-sim.yaml`、`application-test.yaml`，在 `upstream_vars`（或对应位置）增加：

```yaml
upstream_vars:
  bcsfuse_server_url: "http://127.0.0.1:8765"
```

各环境的具体值由 SRE / 部署配置提供；dev/test 可先用本地地址占位。

- [ ] **Step 5: 验证 YAML 语法**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/ocb/src/gateway
uv run python - <<'PY'
import yaml, glob
for p in glob.glob("configs/application*.yaml"):
    yaml.safe_load(open(p))
    print(f"ok: {p}")
PY
```

Expected: 所有 YAML 文件解析成功。

- [ ] **Step 6: Commit**

```bash
cd /Users/wenyang/proj/alpharisk/ocb
git add src/gateway/configs/application.yaml src/gateway/configs/application-*.yaml
git commit -m "feat(gateway): wire bcsfuse upstream, domains, and route security

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 添加 ocb gateway 单元测试

**Files:**
- Modify: `ocb/src/gateway/tests/unit/core/forwarding/test_domain_map.py`（如存在）
- 或新增测试文件。

- [ ] **Step 1: domain map test**

```python
def test_shipped_config_routes_bcsfuse_clean_paths(domain_map):
    domain = domain_map.http_domain_for("/openapi/v1/bcsfuse/groups/g-1/fuse")
    assert domain is not None
    assert domain.server.name == "bcsfuse"

    domain = domain_map.http_domain_for("/openapi/v1/bcsfuse/workers/w-1/config")
    assert domain is not None
    assert domain.server.name == "bcsfuse"
```

- [ ] **Step 2: served openapi security test**

```python
def test_bcsfuse_paths_served_with_user_security(forwarding):
    openapi = forwarding.served_openapi()
    paths = openapi.get("paths", {})
    path = "/openapi/v1/bcsfuse/groups/{group_id}/fuse"
    assert path in paths
    operation = paths[path]["post"]
    security = operation.get("security", [])
    assert any("user" in s for s in security)
```

- [ ] **Step 3: 运行测试**

Run:
```bash
cd /Users/wenyang/proj/alpharisk/ocb/src/gateway
uv run pytest tests/unit/core/forwarding/test_domain_map.py tests/unit/core/forwarding/test_served_openapi.py -v
```

Expected: 测试通过。

- [ ] **Step 4: Commit**

```bash
git commit -am "test(gateway): assert bcsfuse domain and openapi security

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 本地 / 测试环境转发验证

- [ ] **Step 1: 启动内部 bcsfuse**

按 bcsfuse 仓库的 `conf/docker/scripts/admin/bin/start.sh` 或 singlebox 脚本启动；确认监听 `${bcsfuse_server_url}` 对应的端口（默认 8765）。

- [ ] **Step 2: 启动 ocb gateway**

运行 ocb gateway 的启动脚本，确保加载了修改后的 `application.yaml`。

- [ ] **Step 3: 校验 `/openapi.json`**

```bash
curl -H "Authorization: Bearer <token>" https://gateway/openapi.json | \
  jq '.paths | keys | map(select(startswith("/openapi/v1/bcsfuse")))'
```

Expected: 输出包含 fusion 和 workers 路径。

- [ ] **Step 4: 转发 fusion endpoint**

```bash
curl -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -X POST \
     https://gateway/openapi/v1/bcsfuse/groups/<group_id>/fuse \
     -d '{"question":"...","participants":["..."]}'
```

Expected: 返回 200 或业务错误（非 404 / 非 gateway auth 错误），证明流量已转发到 bcsfuse。

- [ ] **Step 5: 转发 workers config endpoint**

```bash
curl -H "Authorization: Bearer <token>" \
     https://gateway/openapi/v1/bcsfuse/workers/<worker_id>/config
```

Expected: 返回 worker config 或业务错误（非 404）。

---

## 自评检查

- **Spec coverage:** bcsfuse server、两个 domain、rewrite、route_security、schema、env overlay、测试已全覆盖。
- **Placeholder scan:** 无 TBD/TODO；`bcsfuse_server_url` 值由环境决定。
- **Type consistency:** 不适用（纯配置变更）。
