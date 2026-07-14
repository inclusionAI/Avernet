# BCSFuse Open-Core API Reference

**Version:** 4.0
**Last Updated:** 2026-07-09

---

## Route Prefix Convention

BCSFuse routes are organized into three prefix tiers by audience and privilege:

| Prefix | Audience | Description |
|--------|----------|-------------|
| `/api/v1` | External product APIs | For 3rd-party integrations (BCS, AI services) |
| `/v1` | Management platform APIs | For admin portal and operational read/write |
| `/v1/admin` | Privileged admin APIs | Destructive / write-heavy operations — only exposed when `BCSFUSE_EXPOSE_ADMIN=true` |

---

## Table of Contents

- [Authentication](#authentication)
- [Health & Diagnostics](#health--diagnostics)
- [External Product APIs (/api/v1)](#external-product-apis-apiv1)
- [Management Platform APIs (/v1)](#management-platform-apis-v1)
- [Privileged Admin APIs (/v1/admin)](#privileged-admin-apis-v1admin)
- [Error Handling](#error-handling)
- [Rate Limiting](#rate-limiting)

---

## Authentication

All API endpoints (except `/health` and `/ready`) require Bearer token authentication.

### Authentication Header

```http
Authorization: Bearer <BCSFUSE_AUTH_TOKEN>
```

### Default Token (Development)

```bash
export BCSFUSE_AUTH_TOKEN="dev-opencore-token"
```

### Example Request

```bash
curl -X GET http://localhost:8765/v1/workers \
  -H "Authorization: Bearer dev-opencore-token"
```

### Error Response (Unauthorized)

```json
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Missing or invalid authentication token"
  }
}
```

---

## Health & Diagnostics

### GET /health

**Purpose:** Shallow health check without provider initialization

**Authentication:** Not required

**Response:**

```json
{
  "status": "ok",
  "startup_profile": "opensource",
  "provider_mode": "runtime",
  "process_health": "alive"
}
```

**Status Codes:**

- `200 OK`: Service is healthy

**Example:**

```bash
curl http://localhost:8765/health
```

---

### GET /ready

**Purpose:** Deep readiness check with provider validation

**Authentication:** Not required

**Response:**

```json
{
  "ready": true,
  "provider_mode": "runtime",
  "providers": 17,
  "vector_store_available": true,
  "vector_store_type": "QdrantLocalVectorStore",
  "vector_store_instance_id": 4683691264,
  "qdrant_collection_name": "bcsfuse_profiles",
  "qdrant_storage_path": "/path/to/.runtime/data/qdrant"
}
```

**Status Codes:**

- `200 OK`: Service is ready
- `503 Service Unavailable`: Provider not initialized or failed

**Example:**

```bash
curl http://localhost:8765/ready
```

---

## External Product APIs (/api/v1)

These endpoints are intended for 3rd-party callers such as BCS. They use the `/api/v1` prefix.

### POST /api/v1/workers/{worker_id}/sync

**Purpose:** Atomic sync: create/update worker, set online, and upsert profile

**Authentication:** Required

> **Backward Compatibility:** `POST /v1/workers/{worker_id}/sync` is also available (deprecated) for existing BCS callers. Prefer `/api/v1/workers/{worker_id}/sync` going forward.

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Unique worker identifier |

**Request Body:**

```json
{
  "name": "string (required)",
  "role": "string (optional)",
  "description": "string (optional)",
  "capabilities": ["string"],
  "visibility": "public|private (default: public)",
  "profile": {
    "summary": "string",
    "expertise": ["string"],
    "scenarios": ["string"],
    "constraints": ["string"]
  }
}
```

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "name": "My Bot",
  "role": "AI Assistant",
  "description": "Expert in NLP",
  "capabilities": ["nlp", "search"],
  "visibility": "public",
  "runtime_state": "online",
  "profile_uploaded": true,
  "created_at": "2026-07-06T10:00:00Z",
  "updated_at": "2026-07-06T10:00:00Z"
}
```

**Status Codes:**

- `200 OK`: Worker synced successfully
- `400 Bad Request`: Invalid request body
- `401 Unauthorized`: Missing or invalid token
- `422 Unprocessable Entity`: Validation error

**Example:**

```bash
curl -X POST http://localhost:8765/api/v1/workers/my-bot-001/sync \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Bot",
    "role": "AI Assistant",
    "capabilities": ["nlp", "search"],
    "visibility": "public",
    "profile": {
      "summary": "Expert in NLP and search",
      "expertise": ["Python", "Machine Learning"],
      "scenarios": ["question answering", "information retrieval"]
    }
  }'
```

---

### PUT /api/v1/workers/{worker_id}/online

**Purpose:** Set worker online

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "runtime_state": "online",
  "updated_at": "2026-07-06T10:00:00Z"
}
```

**Status Codes:**

- `200 OK`: Worker set online successfully
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

**Example:**

```bash
curl -X PUT http://localhost:8765/api/v1/workers/my-bot-001/online \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### PUT /api/v1/workers/{worker_id}/offline

**Purpose:** Set worker offline

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "runtime_state": "offline",
  "updated_at": "2026-07-06T10:00:00Z"
}
```

**Status Codes:**

- `200 OK`: Worker set offline successfully
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

**Example:**

```bash
curl -X PUT http://localhost:8765/api/v1/workers/my-bot-001/offline \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### POST /api/v1/search

**Purpose:** Semantic search for workers based on query

**Authentication:** Required

**Request Body:**

```json
{
  "query": "string (required)",
  "top_k": "int (default: 10, max: 50)",
  "mode": "string (default: auto)",
  "min_score": "float (default: 0.0)",
  "filters": {
    "runtime_state": "online|offline",
    "visibility": "public|private",
    "test_id": "string"
  }
}
```

**Response:**

```json
{
  "success": true,
  "query": "Python expert",
  "results": [
    {
      "worker_id": "bot-001",
      "name": "Python Bot",
      "role": "Python Expert",
      "score": 0.95,
      "runtime_state": "online",
      "visibility": "public",
      "profile_summary": "Expert in Python and ML"
    }
  ],
  "total": 1,
  "search_mode": "semantic"
}
```

**Status Codes:**

- `200 OK`: Search successful
- `400 Bad Request`: Invalid query
- `401 Unauthorized`: Missing or invalid token
- `422 Unprocessable Entity`: Validation error

**Example:**

```bash
curl -X POST http://localhost:8765/api/v1/search \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Python expert with machine learning experience",
    "top_k": 5,
    "filters": {
      "runtime_state": "online"
    }
  }'
```

---

### POST /api/v1/recommend

**Purpose:** Recommend experts for a specific task/question

**Authentication:** Required

**Request Body:**

```json
{
  "question": "string (required)",
  "context": {
    "domain": "string",
    "urgency": "low|medium|high",
    "preferred_experts": ["worker_id"]
  },
  "top_k": "int (default: 3, max: 10)",
  "recommend_to_fuse": "boolean (default: false)"
}
```

**Response:**

```json
{
  "success": true,
  "question": "How do I optimize a slow SQL query?",
  "recommendations": [
    {
      "worker_id": "dba-expert",
      "name": "Database Expert",
      "role": "DBA",
      "score": 0.98,
      "runtime_state": "online",
      "relevance_reason": "Specialized in database optimization",
      "profile_summary": "Expert in SQL optimization and indexing"
    }
  ],
  "total": 1
}
```

**Status Codes:**

- `200 OK`: Recommendation successful
- `400 Bad Request`: Invalid question
- `401 Unauthorized`: Missing or invalid token
- `422 Unprocessable Entity`: Validation error

**Example:**

```bash
curl -X POST http://localhost:8765/api/v1/recommend \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "How do I optimize a slow SQL query?",
    "context": {
      "domain": "database",
      "urgency": "high"
    },
    "top_k": 3,
    "recommend_to_fuse": true
  }'
```

---

### POST /api/v1/groups/{group_id}/fuse

**Purpose:** Multi-expert group consultation (G2 conflict resolution, G5 risk assessment)

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `group_id` | string | Yes | Unique group identifier |

**Request Body:**

```json
{
  "question": "string (required)",
  "participants": [
    {
      "worker_id": "string",
      "profile_key": "worker_id:profile_id"
    }
  ],
  "context": {
    "domain": "string",
    "urgency": "low|medium|high"
  },
  "fusion_mode": "g2|g5 (default: g2)"
}
```

**Response (G2):**

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
    "resolution_strategy": "Implement suggested optimizations",
    "reasoning": "Based on expert analysis...",
    "priority_actions": ["Action 1", "Action 2"],
    "risks": ["Risk 1", "Risk 2"]
  },
  "duration_ms": 5432
}
```

**Response (G5):**

```json
{
  "success": true,
  "group_id": "grp-001",
  "fusion_id": "fus-xyz789",
  "question": "Production deployment risk assessment",
  "fusion_mode": "g5",
  "perspectives": [
    {
      "participant_id": "security-expert:default",
      "perspective": "From a security perspective...",
      "confidence": 0.95,
      "status": "completed"
    }
  ],
  "risk_assessment": {
    "overall_risk": "medium",
    "go_live_conditions": ["Condition 1", "Condition 2"],
    "critical_issues": ["Issue 1", "Issue 2"],
    "recommendations": ["Recommendation 1", "Recommendation 2"]
  },
  "duration_ms": 6543
}
```

**Status Codes:**

- `200 OK`: Fusion successful
- `400 Bad Request`: Invalid request
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Participant not found or offline
- `422 Unprocessable Entity`: Validation error
- `500 Internal Server Error`: LLM or embedding service error

**Example:**

```bash
# G2: Conflict Resolution
curl -X POST http://localhost:8765/api/v1/groups/grp-001/fuse \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What are the trade-offs between MySQL vs PostgreSQL?",
    "participants": [
      {
        "worker_id": "dba-expert",
        "profile_key": "dba-expert:default"
      },
      {
        "worker_id": "backend-arch",
        "profile_key": "backend-arch:default"
      }
    ],
    "fusion_mode": "g2"
  }'

# G5: Risk Assessment
curl -X POST http://localhost:8765/api/v1/groups/grp-002/fuse \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Production deployment risk assessment for new feature",
    "participants": [
      {
        "worker_id": "security-expert",
        "profile_key": "security-expert:default"
      },
      {
        "worker_id": "ops-expert",
        "profile_key": "ops-expert:default"
      }
    ],
    "fusion_mode": "g5"
  }'
```

---

## Management Platform APIs (/v1)

These endpoints are intended for the admin/management portal. They use the `/v1` prefix.

### GET /v1/workers

**Purpose:** List all workers with optional filtering

**Authentication:** Required

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `offset` | int | 0 | Pagination offset |
| `limit` | int | 20 | Maximum items (max: 100) |
| `runtime_state` | string | - | Filter by state (online/offline) |
| `visibility` | string | - | Filter by visibility (public/private) |
| `test_id` | string | - | Filter by test_id |

**Response:**

```json
{
  "success": true,
  "workers": [
    {
      "worker_id": "bot-001",
      "name": "Bot 1",
      "role": "Assistant",
      "runtime_state": "online",
      "visibility": "public",
      "created_at": "2026-07-06T10:00:00Z"
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 20
}
```

**Status Codes:**

- `200 OK`: Success
- `401 Unauthorized`: Missing or invalid token

**Example:**

```bash
# List all workers
curl -X GET "http://localhost:8765/v1/workers" \
  -H "Authorization: Bearer dev-opencore-token"

# List online workers
curl -X GET "http://localhost:8765/v1/workers?runtime_state=online" \
  -H "Authorization: Bearer dev-opencore-token"

# Paginate
curl -X GET "http://localhost:8765/v1/workers?offset=0&limit=10" \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### GET /v1/workers/{worker_id}

**Purpose:** Get worker details

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |

**Response:**

```json
{
  "success": true,
  "worker": {
    "worker_id": "my-bot-001",
    "name": "My Bot",
    "role": "AI Assistant",
    "description": "Expert in NLP",
    "capabilities": ["nlp", "search"],
    "visibility": "public",
    "runtime_state": "online",
    "created_at": "2026-07-06T10:00:00Z",
    "updated_at": "2026-07-06T10:00:00Z"
  }
}
```

**Status Codes:**

- `200 OK`: Success
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

**Example:**

```bash
curl -X GET http://localhost:8765/v1/workers/my-bot-001 \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### POST /v1/workers/batch

**Purpose:** Batch query multiple workers

**Authentication:** Required

**Request Body:**

```json
{
  "worker_ids": ["bot-001", "bot-002", "bot-003"]
}
```

**Response:**

```json
{
  "success": true,
  "data": {
    "bot-001": {
      "worker_id": "bot-001",
      "name": "Bot 1",
      "runtime_state": "online"
    },
    "bot-002": {
      "worker_id": "bot-002",
      "name": "Bot 2",
      "runtime_state": "offline"
    }
  },
  "not_found_ids": ["bot-003"]
}
```

**Status Codes:**

- `200 OK`: Success
- `401 Unauthorized`: Missing or invalid token
- `422 Unprocessable Entity`: Validation error

**Example:**

```bash
curl -X POST http://localhost:8765/v1/workers/batch \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "worker_ids": ["bot-001", "bot-002"]
  }'
```

---

### PUT /v1/workers/{worker_id}/profiles/{profile_id}

**Purpose:** Upsert worker profile

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |
| `profile_id` | string | Yes | Profile identifier (usually "default") |

**Request Body:**

```json
{
  "profile": {
    "summary": "string (required)",
    "expertise": ["string"],
    "scenarios": ["string"],
    "constraints": ["string"],
    "custom_fields": {}
  }
}
```

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "profile_id": "default",
  "profile_key": "my-bot-001:default",
  "uploaded": true,
  "created_at": "2026-07-06T10:00:00Z"
}
```

**Status Codes:**

- `200 OK`: Profile upserted successfully
- `400 Bad Request`: Invalid request body
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found
- `422 Unprocessable Entity`: Validation error

**Example:**

```bash
curl -X PUT http://localhost:8765/v1/workers/my-bot-001/profiles/default \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "profile": {
      "summary": "Expert AI assistant for technical queries",
      "expertise": ["Python", "SQL", "Machine Learning"],
      "scenarios": ["code review", "debugging", "architecture design"],
      "constraints": ["No production access", "Read-only operations"]
    }
  }'
```

---

### GET /v1/workers/{worker_id}/profiles

**Purpose:** List all profiles for a worker

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "profiles": [
    {
      "profile_id": "default",
      "profile_key": "my-bot-001:default",
      "is_active": true,
      "created_at": "2026-07-06T10:00:00Z",
      "updated_at": "2026-07-06T10:00:00Z"
    }
  ]
}
```

**Status Codes:**

- `200 OK`: Success
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

**Example:**

```bash
curl -X GET http://localhost:8765/v1/workers/my-bot-001/profiles \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### GET /v1/workers/{worker_id}/profiles/{profile_id}

**Purpose:** Get specific profile content

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |
| `profile_id` | string | Yes | Profile identifier |

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "profile_id": "default",
  "profile": {
    "summary": "Expert AI assistant for technical queries",
    "expertise": ["Python", "SQL", "Machine Learning"],
    "scenarios": ["code review", "debugging"],
    "constraints": ["No production access"]
  },
  "created_at": "2026-07-06T10:00:00Z",
  "updated_at": "2026-07-06T10:00:00Z"
}
```

**Status Codes:**

- `200 OK`: Success
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker or profile not found

**Example:**

```bash
curl -X GET http://localhost:8765/v1/workers/my-bot-001/profiles/default \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### PATCH /v1/workers/{worker_id}/profiles/{profile_id}

**Purpose:** Partial update of worker profile (only update provided fields)

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |
| `profile_id` | string | Yes | Profile identifier |

**Request Body:**

```json
{
  "display_name": "string (optional)",
  "soul_md": "string (optional)",
  "agents_md": "string (optional)",
  "tools_md": "string (optional)",
  "boot_md": "string (optional)",
  "heartbeat_md": "string (optional)",
  "contents": {
    "custom.md": "New content",
    "capabilities": ["skill1", "skill2"]
  },
  "contents_delete": ["old.md", "temp.md"],
  "skill_sets": [
    {
      "name": "Skill Name",
      "description": "Skill description",
      "content": "Detailed content"
    }
  ],
  "metadata": {
    "version": "2.0",
    "updated_by": "admin"
  },
  "metadata_delete": ["old_field"],
  "activate": false
}
```

**Key Features:**

- Only updates provided fields, unprovided fields remain unchanged
- `contents`: Incremental update (add/replace keys, not delete)
- `contents_delete`: Specify keys to delete from contents
- `metadata`: Incremental update (add/replace keys, not delete)
- `metadata_delete`: Specify keys to delete from metadata
- `skill_sets`: If provided, replaces all (cannot partial update)

**Response:**

```json
{
  "worker_id": "my-bot-001",
  "profile_id": "default",
  "display_name": "Updated Name",
  "soul_md": "# Core Identity...",
  "agents_md": "# Work Configuration...",
  "tools_md": "# Tool Configuration...",
  "boot_md": "# Boot Configuration...",
  "heartbeat_md": "# Heartbeat...",
  "contents": {
    "profile": "Semantic profile",
    "capabilities": ["skill1", "skill2"],
    "custom.md": "New content"
  },
  "skill_sets": [
    {
      "name": "Skill Name",
      "description": "Skill description",
      "content": "Detailed content"
    }
  ],
  "metadata": {
    "version": "2.0",
    "updated_by": "admin"
  },
  "content_type": "markdown",
  "is_active": true,
  "version": 2,
  "quality_score": 0.85,
  "quality_issues": [],
  "created_at": "2026-07-06T10:00:00Z",
  "updated_at": "2026-07-06T11:00:00Z"
}
```

**Status Codes:**

- `200 OK`: Profile updated successfully
- `400 Bad Request`: Invalid request body
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker or profile not found
- `422 Unprocessable Entity`: Validation error

**Example:**

```bash
# Update display name and add custom content
curl -X PATCH http://localhost:8765/v1/workers/my-bot-001/profiles/default \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Updated Bot Name",
    "contents": {
      "custom.md": "# Custom Instructions\nNew instructions here"
    },
    "metadata": {
      "version": "2.0"
    }
  }'

# Remove old content and update capabilities
curl -X PATCH http://localhost:8765/v1/workers/my-bot-001/profiles/default \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": {
      "capabilities": ["python", "ml", "data-science"]
    },
    "contents_delete": ["old_instructions.md", "deprecated.md"]
  }'
```

**Difference from PUT:**

| Aspect | PUT | PATCH |
|--------|-----|-------|
| Scope | Full replacement | Partial update |
| Unprovided fields | Cleared/reset | Preserved |
| Contents update | Replace all | Merge with existing |
| Use case | Full profile update | Incremental changes |

---

### PUT /v1/workers/{worker_id}/trust-level

**Purpose:** Update worker trust level

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |

**Request Body:**

```json
{
  "trust_level": "unverified|verified|trusted|privileged"
}
```

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "trust_level": "trusted",
  "updated_at": "2026-07-09T10:00:00Z"
}
```

**Status Codes:**

- `200 OK`: Trust level updated
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found
- `422 Unprocessable Entity`: Validation error

**Example:**

```bash
curl -X PUT http://localhost:8765/v1/workers/my-bot-001/trust-level \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{"trust_level": "trusted"}'
```

---

### PUT /v1/workers/{worker_id}/profiles/{profile_id}/activate

**Purpose:** Activate a profile

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |
| `profile_id` | string | Yes | Profile identifier |

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "profile_id": "default",
  "is_active": true
}
```

**Status Codes:**

- `200 OK`: Profile activated
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker or profile not found

**Example:**

```bash
curl -X PUT http://localhost:8765/v1/workers/my-bot-001/profiles/default/activate \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### POST /v1/workers/profiles/search

**Purpose:** Search profiles across workers

**Authentication:** Required

**Request Body:**

```json
{
  "query": "string (required)",
  "top_k": 10,
  "filters": {}
}
```

**Response:**

```json
{
  "success": true,
  "results": [
    {
      "worker_id": "bot-001",
      "profile_id": "default",
      "score": 0.92
    }
  ],
  "total": 1
}
```

**Status Codes:**

- `200 OK`: Search successful
- `401 Unauthorized`: Missing or invalid token

**Example:**

```bash
curl -X POST http://localhost:8765/v1/workers/profiles/search \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{"query": "Python expert", "top_k": 5}'
```

---

### GET /v1/workers/{worker_id}/profiles/quality

**Purpose:** Get quality score for worker's profiles

**Authentication:** Required

**Response:**

```json
{
  "worker_id": "my-bot-001",
  "quality_score": 0.85,
  "quality_issues": []
}
```

**Status Codes:**

- `200 OK`: Success
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

**Example:**

```bash
curl http://localhost:8765/v1/workers/my-bot-001/profiles/quality \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### GET /v1/workers/{worker_id}/profiles/{profile_id}/quality

**Purpose:** Get quality score for a specific profile

**Authentication:** Required

**Response:**

```json
{
  "worker_id": "my-bot-001",
  "profile_id": "default",
  "quality_score": 0.85,
  "quality_issues": []
}
```

**Status Codes:**

- `200 OK`: Success
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker or profile not found

---

### GET /v1/workers/profiles/active-profiles

**Purpose:** List all active profiles across workers

**Authentication:** Required

**Response:**

```json
{
  "success": true,
  "profiles": [
    {
      "worker_id": "bot-001",
      "profile_id": "default",
      "profile_key": "bot-001:default",
      "is_active": true
    }
  ],
  "total": 1
}
```

---

### GET /v1/workers/{worker_id}/config

**Purpose:** Get worker configuration

**Authentication:** Required

**Response:**

```json
{
  "worker_id": "my-bot-001",
  "config": {
    "max_concurrent_tasks": 5,
    "timeout_seconds": 30
  }
}
```

**Status Codes:**

- `200 OK`: Success
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

---

### GET /v1/workers/config/by-source

**Purpose:** Get workers grouped by source

**Authentication:** Required

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source` | string | No | Filter by source type |

**Response:**

```json
{
  "success": true,
  "sources": {
    "api": ["bot-001", "bot-002"],
    "bcs": ["bot-003"]
  }
}
```

---

### POST /v1/verify/batch

**Purpose:** Batch verification of workers

**Authentication:** Required

**Request Body:**

```json
{
  "worker_ids": ["bot-001", "bot-002"],
  "reset_trust_level": false
}
```

**Response:**

```json
{
  "success": true,
  "results": [
    {"worker_id": "bot-001", "verified": true},
    {"worker_id": "bot-002", "verified": true}
  ]
}
```

**Status Codes:**

- `200 OK`: Verification successful
- `401 Unauthorized`: Missing or invalid token
- `503 Service Unavailable`: Verification service not available

**Example:**

```bash
curl -X POST http://localhost:8765/v1/verify/batch \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{"worker_ids": ["bot-001", "bot-002"]}'
```

---

### POST /v1/verify/batchAll

**Purpose:** Verify all workers

**Authentication:** Required

**Request Body:**

```json
{
  "reset_trust_level": false,
  "dry_run": false
}
```

**Response:**

```json
{
  "success": true,
  "total_verified": 15,
  "results": []
}
```

**Status Codes:**

- `200 OK`: Verification successful
- `401 Unauthorized`: Missing or invalid token
- `503 Service Unavailable`: Verification service not available

---

## Privileged Admin APIs (/v1/admin)

These endpoints perform destructive or write-heavy operations (create/delete resources, overwrite configuration). They are only exposed when `BCSFUSE_EXPOSE_ADMIN=true` and use the `/v1/admin` prefix.

### POST /v1/admin/workers

**Purpose:** Create a new worker

**Authentication:** Required

**Request Body:**

```json
{
  "worker_id": "string (required)",
  "name": "string (required)",
  "description": "string (optional)",
  "skills": ["string"],
  "is_public": true
}
```

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "name": "My Bot"
}
```

**Status Codes:**

- `200 OK`: Worker created
- `401 Unauthorized`: Missing or invalid token
- `409 Conflict`: Worker already exists
- `422 Unprocessable Entity`: Validation error

**Example:**

```bash
curl -X POST http://localhost:8765/v1/admin/workers \
  -H "Authorization: Bearer dev-opencore-token" \
  -H "Content-Type: application/json" \
  -d '{
    "worker_id": "my-bot-001",
    "name": "My Bot",
    "description": "Test worker"
  }'
```

---

### DELETE /v1/admin/workers/{worker_id}

**Purpose:** Delete worker and associated data

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |

**Response:**

```json
{
  "success": true,
  "message": "Worker deleted",
  "worker_id": "my-bot-001"
}
```

**Status Codes:**

- `200 OK`: Worker deleted successfully
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

**Example:**

```bash
curl -X DELETE http://localhost:8765/v1/admin/workers/my-bot-001 \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### DELETE /v1/admin/workers/{worker_id}/profiles/{profile_id}

**Purpose:** Delete a profile

**Authentication:** Required

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `worker_id` | string | Yes | Worker identifier |
| `profile_id` | string | Yes | Profile identifier |

**Response:**

```json
{
  "success": true,
  "message": "Profile deleted",
  "worker_id": "my-bot-001",
  "profile_id": "default"
}
```

**Status Codes:**

- `200 OK`: Profile deleted successfully
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker or profile not found

**Example:**

```bash
curl -X DELETE http://localhost:8765/v1/admin/workers/my-bot-001/profiles/default \
  -H "Authorization: Bearer dev-opencore-token"
```

---

### PATCH /v1/admin/workers/{worker_id}

**Purpose:** Partial update worker metadata

**Authentication:** Required

**Request Body:**

```json
{
  "name": "string (optional)",
  "description": "string (optional)"
}
```

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "patched": true
}
```

**Status Codes:**

- `200 OK`: Worker updated
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

---

### PUT /v1/admin/workers/{worker_id}/config

**Purpose:** Update worker configuration

**Authentication:** Required

**Request Body:**

```json
{
  "config": {
    "max_concurrent_tasks": 10,
    "timeout_seconds": 60
  }
}
```

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "config": {
    "max_concurrent_tasks": 10,
    "timeout_seconds": 60
  }
}
```

**Status Codes:**

- `200 OK`: Config updated
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker not found

---

### POST /v1/admin/workers/config/batch

**Purpose:** Batch update worker configurations

**Authentication:** Required

**Request Body:**

```json
{
  "updates": [
    {"worker_id": "bot-001", "config": {"timeout_seconds": 60}},
    {"worker_id": "bot-002", "config": {"timeout_seconds": 90}}
  ]
}
```

**Response:**

```json
{
  "success": true,
  "updated": 2,
  "errors": []
}
```

---

### POST /v1/admin/workers/{worker_id}/profiles/{profile_id}/analyze

**Purpose:** Trigger LLM-based profile analysis

**Authentication:** Required

**Request Body:**

```json
{
  "analysis_type": "quality|completeness|consistency",
  "provider": "openai|anthropic (optional)"
}
```

**Response:**

```json
{
  "success": true,
  "worker_id": "my-bot-001",
  "profile_id": "default",
  "analysis": {
    "quality_score": 0.85,
    "suggestions": ["Add more detail to expertise section"]
  }
}
```

**Status Codes:**

- `200 OK`: Analysis completed
- `401 Unauthorized`: Missing or invalid token
- `404 Not Found`: Worker or profile not found
- `503 Service Unavailable`: LLM service not available

---

## Removed Endpoints

The following endpoints are no longer exposed in OSS deployment:

| Method | Endpoint | Reason |
|--------|----------|--------|
| GET | `/providers` | Internal topology detail, not intended for external exposure |
| GET | `/v1/providers/status` | Debug/diagnostics endpoint |
| GET | `/v1/search/stats` | Debug/diagnostics endpoint |
| GET | `/v1/admin/vector-store/scheduler-status` | ZDAS-only, not applicable in OSS |
| POST | `/v1/admin/vector-store/start-scheduler` | ZDAS-only, not applicable in OSS |
| GET | `/v1/__diagnostics/open-core/runtime-fingerprint` | Internal runtime detail, not intended for external exposure |

---

## Integration Examples

### BCS Worker Registration

```bash
# 1. Register worker with profile (atomic: create + online + profile)
curl -X POST http://localhost:8765/api/v1/workers/my-bot/sync \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Bot",
    "runtime_state": "online",
    "profile": {
      "profile_id": "default",
      "summary": "AI assistant for code review",
      "expertise": ["Python", "Code Review"]
    }
  }'

# 2. Set offline (maintenance)
curl -X PUT http://localhost:8765/api/v1/workers/my-bot/offline \
  -H "Authorization: Bearer $TOKEN"

# 3. Set online again
curl -X PUT http://localhost:8765/api/v1/workers/my-bot/online \
  -H "Authorization: Bearer $TOKEN"

# 4. Update availability
curl -X PUT http://localhost:8765/api/v1/workers/my-bot/availability \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"availability": "protected"}'
```

### AI Service Integration

```bash
# 1. Search for matching workers
curl -X POST http://localhost:8765/api/v1/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "database optimization", "top_k": 3}'

# 2. Get recommendations
curl -X POST http://localhost:8765/api/v1/recommend \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question": "How to optimize slow SQL queries", "top_k": 3}'

# 3. Multi-expert fusion
curl -X POST http://localhost:8765/api/v1/groups/grp-001/fuse \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Should we migrate from MySQL to PostgreSQL?",
    "participants": [
      {"worker_id": "dba-expert", "profile_key": "dba-expert:default"},
      {"worker_id": "backend-arch", "profile_key": "backend-arch:default"}
    ],
    "fusion_mode": "g2"
  }'
```

### Management Portal Operations

```bash
# List all workers
curl http://localhost:8765/v1/workers \
  -H "Authorization: Bearer $TOKEN"

# Get worker details
curl http://localhost:8765/v1/workers/my-bot \
  -H "Authorization: Bearer $TOKEN"

# List profiles
curl http://localhost:8765/v1/workers/my-bot/profiles \
  -H "Authorization: Bearer $TOKEN"

# Activate a profile
curl -X PUT http://localhost:8765/v1/workers/my-bot/profiles/default/activate \
  -H "Authorization: Bearer $TOKEN"

# Check profile quality
curl http://localhost:8765/v1/workers/my-bot/profiles/quality \
  -H "Authorization: Bearer $TOKEN"

# Update trust level
curl -X PUT http://localhost:8765/v1/workers/my-bot/trust-level \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"trust_level": "trusted"}'
```

### Python SDK Example

```python
import requests

BASE = "http://localhost:8765"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# Register worker
resp = requests.post(
    f"{BASE}/api/v1/workers/my-bot/sync",
    headers=HEADERS,
    json={
        "name": "My Bot",
        "runtime_state": "online",
        "profile": {
            "profile_id": "default",
            "summary": "AI code reviewer",
            "expertise": ["Python", "Code Review"],
        },
    },
)
print(resp.json())

# Search
resp = requests.post(
    f"{BASE}/api/v1/search",
    headers=HEADERS,
    json={"query": "code review expert", "top_k": 5},
)
print(resp.json())

# Recommend
resp = requests.post(
    f"{BASE}/api/v1/recommend",
    headers=HEADERS,
    json={"question": "Review this Python code", "top_k": 3},
)
print(resp.json())
```

---

## Error Handling

### Standard Error Response Format

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable error message",
    "details": {
      "field": "Additional context"
    }
  }
}
```

### Common Error Codes

| Code | HTTP Status | Description |
|------|------------|-------------|
| `UNAUTHORIZED` | 401 | Missing or invalid authentication token |
| `NOT_FOUND` | 404 | Resource not found |
| `VALIDATION_ERROR` | 422 | Request validation failed |
| `PROVIDER_ERROR` | 500 | Internal provider error |
| `LLM_ERROR` | 500 | LLM service error |
| `EMBEDDING_ERROR` | 500 | Embedding service error |
| `VECTOR_STORE_ERROR` | 500 | Vector store error |
| `SERVICE_UNAVAILABLE` | 503 | Service not ready |

### Validation Error Example

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": {
      "field": "query",
      "message": "Field required"
    }
  }
}
```

---

## Rate Limiting

**Open-Source Deployment:** No rate limiting by default.

**Production Recommendations:**

- Implement rate limiting at reverse proxy (nginx, traefik)
- Suggested limits: 100 requests/minute per IP
- Burst allowance: 20 requests

---

## Pagination

List endpoints support pagination via query parameters:

- `offset`: Starting position (default: 0)
- `limit`: Maximum items (default: 20, max: 100)

**Example:**

```bash
curl -X GET "http://localhost:8765/v1/workers?offset=20&limit=20" \
  -H "Authorization: Bearer dev-opencore-token"
```

**Response includes pagination metadata:**

```json
{
  "success": true,
  "workers": [...],
  "total": 45,
  "offset": 20,
  "limit": 20
}
```

---

## OpenAPI Specification

Full OpenAPI specification available at:

```
GET http://localhost:8765/openapi.json
```

**Download:**

```bash
curl http://localhost:8765/openapi.json -o bcsfuse-openapi.json
```

**View in Swagger UI:**

```bash
# Using swagger-ui-watcher
npm install -g swagger-ui-watcher
swagger-ui-watcher bcsfuse-openapi.json
```

---

## Next Steps

- **External API Doc**: See `api-external.md` for the external product API reference (7 endpoints only)
- **Deployment**: See `../deploy/open-source-deployment-guide.md`