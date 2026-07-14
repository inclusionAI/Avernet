# BCSFuse External Product API

**Version:** 1.0
**Last Updated:** 2026-07-09

---

> 本文档仅覆盖 `/api/v1` 前缀的外部产品 API（7 个端点）。
> 管理平台 API 和特权管理 API 详见 `api-reference.md`。

---

## 概述

BCSFuse 外部产品 API 面向第三方系统（如 BCS、AI 集成平台），提供 Worker 注册、状态管理、语义搜索、推荐和融合能力。

### Base URL

```
http://localhost:8765
```

### 认证

所有端点需要 Bearer Token 认证：

```bash
Authorization: Bearer <BCSFUSE_AUTH_TOKEN>
```

### 路由前缀

所有外部产品 API 使用 `/api/v1` 前缀，始终暴露，无需额外配置。

---

## 端点一览

| 方法 | 端点 | 用途 |
|------|------|------|
| POST | `/api/v1/workers/{worker_id}/sync` | 注册/同步 Worker（核心入口） |
| PUT | `/api/v1/workers/{worker_id}/online` | Worker 上线 |
| PUT | `/api/v1/workers/{worker_id}/offline` | Worker 下线 |
| PUT | `/api/v1/workers/{worker_id}/availability` | 设置 Worker 可用性 |
| POST | `/api/v1/search` | 语义搜索 Worker |
| POST | `/api/v1/recommend` | 推荐 Worker |
| POST | `/api/v1/groups/{group_id}/fuse` | 多专家融合（G2/G5） |

> **兼容路径：** `POST /v1/workers/{worker_id}/sync` 仍可使用（供 BCS 存量调用），已标记 deprecated，建议迁移到 `/api/v1`。

---

## 1. POST /api/v1/workers/{worker_id}/sync

注册或同步 Worker，原子操作：创建/更新 Worker → 上线 → 写入/激活 Profile。

### 路径参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `worker_id` | string | 是 | Worker 唯一标识 |

### 请求体

```json
{
  "name": "string (必填)",
  "type": "bot|human (默认: bot)",
  "description": "string (选填)",
  "runtime_state": "online|offline (默认: online)",
  "profile": {
    "profile_id": "string (必填，通常为 'default')",
    "summary": "string",
    "expertise": ["string"],
    "scenarios": ["string"],
    "constraints": ["string"]
  }
}
```

### 响应

```json
{
  "worker_id": "my-bot-001",
  "name": "My Bot",
  "role": "AI Assistant",
  "runtime_state": "online",
  "profile_uploaded": true,
  "created_at": "2026-07-09T10:00:00Z",
  "updated_at": "2026-07-09T10:00:00Z"
}
```

### 状态码

| 码 | 说明 |
|----|------|
| 200 | 同步成功 |
| 400 | 请求体无效 |
| 401 | 未认证 |
| 422 | 参数校验失败 |
| 503 | Provider 不可用 |

### 示例

```bash
curl -X POST http://localhost:8765/api/v1/workers/my-bot-001/sync \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Bot",
    "type": "bot",
    "runtime_state": "online",
    "profile": {
      "profile_id": "default",
      "summary": "Expert in NLP and search",
      "expertise": ["Python", "Machine Learning"],
      "scenarios": ["question answering", "information retrieval"]
    }
  }'
```

---

## 2. PUT /api/v1/workers/{worker_id}/online

将 Worker 设为上线状态。

### 路径参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `worker_id` | string | 是 | Worker 唯一标识 |

### 响应

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "runtime_state": "online",
  "lifecycle_state": "active"
}
```

### 状态码

| 码 | 说明 |
|----|------|
| 200 | 上线成功 |
| 401 | 未认证 |
| 404 | Worker 不存在 |
| 503 | Provider 不可用 |

### 示例

```bash
curl -X PUT http://localhost:8765/api/v1/workers/my-bot-001/online \
  -H "Authorization: Bearer dev-opencore-token"
```

---

## 3. PUT /api/v1/workers/{worker_id}/offline

将 Worker 设为下线状态。

### 路径参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `worker_id` | string | 是 | Worker 唯一标识 |

### 响应

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "runtime_state": "offline",
  "lifecycle_state": "inactive"
}
```

### 状态码

| 码 | 说明 |
|----|------|
| 200 | 下线成功 |
| 401 | 未认证 |
| 404 | Worker 不存在 |

### 示例

```bash
curl -X PUT http://localhost:8765/api/v1/workers/my-bot-001/offline \
  -H "Authorization: Bearer dev-opencore-token"
```

---

## 4. PUT /api/v1/workers/{worker_id}/availability

设置 Worker 可用性级别。

### 路径参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `worker_id` | string | 是 | Worker 唯一标识 |

### 请求体

```json
{
  "availability": "public|protected|private"
}
```

### 响应

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "availability": "public"
}
```

### 状态码

| 码 | 说明 |
|----|------|
| 200 | 设置成功 |
| 401 | 未认证 |
| 404 | Worker 不存在 |
| 422 | 参数校验失败 |

### 示例

```bash
curl -X PUT http://localhost:8765/api/v1/workers/my-bot-001/availability \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{"availability": "public"}'
```

---

## 5. POST /api/v1/search

语义搜索 Worker，基于向量相似度匹配。

### 请求体

```json
{
  "query": "string (必填，搜索关键词)",
  "top_k": 10,
  "mode": "auto",
  "min_score": 0.0,
  "filters": {
    "runtime_state": "online",
    "visibility": "public"
  }
}
```

### 响应

```json
{
  "query": "Python expert",
  "top_k": 10,
  "mode": "semantic",
  "results_count": 3,
  "results": [
    {
      "profile_key": "bot-001:default",
      "worker_id": "bot-001",
      "profile_id": "default",
      "score": 0.95,
      "short_profile": "Expert in Python and ML"
    }
  ],
  "timing_ms": 120.5
}
```

### 状态码

| 码 | 说明 |
|----|------|
| 200 | 搜索成功 |
| 400 | 查询无效 |
| 401 | 未认证 |
| 503 | 搜索服务不可用 |

### 示例

```bash
curl -X POST http://localhost:8765/api/v1/search \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Python expert with machine learning experience",
    "top_k": 5,
    "filters": {"runtime_state": "online"}
  }'
```

---

## 6. POST /api/v1/recommend

根据任务/问题推荐最合适的 Worker。

### 请求体

```json
{
  "question": "string (必填)",
  "context": {
    "domain": "string",
    "urgency": "low|medium|high",
    "preferred_experts": ["worker_id"]
  },
  "top_k": 3,
  "recommend_to_fuse": false
}
```

### 响应

```json
{
  "success": true,
  "question": "How do I optimize a slow SQL query?",
  "recommendations": [
    {
      "worker_id": "dba-expert",
      "name": "Database Expert",
      "score": 0.98,
      "runtime_state": "online",
      "relevance_reason": "Specialized in database optimization"
    }
  ],
  "total": 1
}
```

### 状态码

| 码 | 说明 |
|----|------|
| 200 | 推荐成功 |
| 400 | 参数无效 |
| 401 | 未认证 |
| 422 | 校验失败 |

### 示例

```bash
curl -X POST http://localhost:8765/api/v1/recommend \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How do I optimize a slow SQL query?",
    "context": {"domain": "database", "urgency": "high"},
    "top_k": 3
  }'
```

---

## 7. POST /api/v1/groups/{group_id}/fuse

多专家群体融合，支持 G2（冲突对齐）和 G5（风险评估）两种模式。

### 路径参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `group_id` | string | 是 | 群组唯一标识 |

### 请求体

```json
{
  "question": "string (必填)",
  "participants": [
    {
      "worker_id": "string",
      "profile_key": "worker_id:profile_id"
    }
  ],
  "fusion_mode": "g2|g5"
}
```

### 响应 (G2 模式)

```json
{
  "success": true,
  "group_id": "grp-001",
  "fusion_id": "fus-abc123",
  "question": "Database optimization strategies",
  "fusion_mode": "g2",
  "perspectives": [
    {
      "participant_id": "dba-expert:default",
      "perspective": "From a DBA perspective...",
      "confidence": 0.9,
      "status": "completed"
    }
  ],
  "conclusion": {
    "overall_severity": "medium",
    "go_no_go": "proceed_with_caution",
    "reasoning": "Based on expert analysis..."
  },
  "duration_ms": 5432
}
```

### 状态码

| 码 | 说明 |
|----|------|
| 200 | 融合成功 |
| 400 | 参数无效 |
| 401 | 未认证 |
| 404 | 参与者不存在或离线 |
| 500 | LLM/Embedding 服务错误 |

### 示例

```bash
# G2: 冲突对齐
curl -X POST http://localhost:8765/api/v1/groups/grp-001/fuse \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "MySQL vs PostgreSQL for new microservice?",
    "participants": [
      {"worker_id": "dba-expert", "profile_key": "dba-expert:default"},
      {"worker_id": "backend-arch", "profile_key": "backend-arch:default"}
    ],
    "fusion_mode": "g2"
  }'
```

---

## 典型调用流程

### BCS Worker 注册 + 上线

```bash
# 1. 注册 Worker（原子操作：创建 + 上线 + 写入 Profile）
curl -X POST http://localhost:8765/api/v1/workers/my-bot/sync \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Bot",
    "runtime_state": "online",
    "profile": {
      "profile_id": "default",
      "summary": "AI assistant for code review"
    }
  }'

# 2. 下线（维护时）
curl -X PUT http://localhost:8765/api/v1/workers/my-bot/offline \
  -H "Authorization: Bearer $TOKEN"

# 3. 重新上线
curl -X PUT http://localhost:8765/api/v1/workers/my-bot/online \
  -H "Authorization: Bearer $TOKEN"
```

### AI 服务集成

```bash
# 1. 搜索匹配的 Worker
curl -X POST http://localhost:8765/api/v1/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "数据库优化", "top_k": 3}'

# 2. 获取推荐
curl -X POST http://localhost:8765/api/v1/recommend \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "如何优化慢查询", "top_k": 3}'

# 3. 多专家融合
curl -X POST http://localhost:8765/api/v1/groups/grp-001/fuse \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "是否应该迁移到 PostgreSQL",
    "participants": [
      {"worker_id": "dba-expert", "profile_key": "dba-expert:default"},
      {"worker_id": "backend-arch", "profile_key": "backend-arch:default"}
    ]
  }'
```

---

## 错误码

| 错误码 | HTTP 状态 | 说明 |
|--------|-----------|------|
| `UNAUTHORIZED` | 401 | Token 无效或缺失 |
| `NOT_FOUND` | 404 | 资源不存在 |
| `VALIDATION_ERROR` | 422 | 请求参数校验失败 |
| `PROVIDER_NOT_AVAILABLE` | 503 | 服务未就绪 |

---

## OpenAPI

完整 OpenAPI 规范：

```
GET http://localhost:8765/openapi.json
```