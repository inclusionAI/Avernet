# Fuse API 逻辑详解

## 接口概述

| 属性 | 说明 |
|------|------|
| **端点** | `POST /api/v1/groups/{group_id}/fuse` |
| **功能** | 多参与者视角融合决策 |
| **三种模式** | G1 (agent)、G2 (conflict_alignment)、G5 (expert_diagnosis) |
| **核心职责** | 收集多个 Worker 的专业视角，生成综合建议 |

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    HTTP Layer (fusion_routes.py)                 │
│  - 参数校验 (group_id 格式: grp-[A-Za-z0-9_-]+)                    │
│  - 请求/响应转换                                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│              Application Layer (GroupFusionService)               │
│  - fuse(): 根据 fusion_mode 分发到 G1/G2/G5                      │
│  - _collect_perspectives(): 收集所有参与者视角                    │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│              Domain Layer (PerspectiveProvider)                   │
│  - collect(context): 为单个 participant 收集视角                  │
│                                                                 │
│  两种实现:                                                      │
│  ├─ LLMPerspectiveProvider → 调用真实 LLM                       │
│  └─ StubPerspectiveProvider → 返回预设响应                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 三种模式详解

### 2.1 G1 模式 (agent) - 基础融合

**触发条件**: `fusion_mode="agent"` (默认值)

**处理流程**:
```python
def _fuse_g1(request, group_id):
    1. 生成 fusion_id (uuid)
    2. 确定 driver_bot_id（默认为 participants[0]）
    3. 收集所有参与者视角
    4. 判断是否 partial_success（部分视角收集成功）
    5. 生成 recommendation：
       - 优先使用 LLM (FusionRecommendationService)
       - 回退到规则方法 (_synthesize_recommendation)
    6. 返回 FusionResult
```

**响应字段特点**:
```json
{
  "fusion_mode": "agent",
  "recommendation": { ... },        // 单数，综合建议
  "conflicts": [],                  // 空数组
  "alignment_points": [],           // 空数组
  "risk_assessment": null,          // null
  "critical_issues": [],            // 空数组
  "recommendations": []             // 空数组（复数）
}
```

---

### 2.2 G2 模式 (conflict_alignment) - 冲突对齐

**触发条件**: `fusion_mode="conflict_alignment"`

**处理流程**:
```python
def _fuse_g2(request, group_id):
    1. 复用 G1 视角收集逻辑
    2. 调用 ConflictAlignmentService.align()
       - 分析冲突（冲突方/问题/立场/严重程度）
       - 识别对齐点
       - 生成关键洞察
    3. 返回包含冲突分析的结果
```

**响应字段特点**:
```json
{
  "fusion_mode": "conflict_alignment",
  "conflicts": [
    {
      "parties": ["pm", "dev"],
      "issue": "超时时间设置",
      "positions": ["30分钟", "60分钟"],
      "severity": "medium"
    }
  ],
  "alignment_points": [
    { "summary": "都认可需要优化性能" }
  ],
  "key_insights": ["冲突主要集中在时间设置"],
  "conclusion": { ... },            // 冲突结论
  "risk_assessment": null             // G5 字段为空
}
```

**V2 Feature Flags** (可选结构化输出):
- `ENABLE_G2_STRUCTURED_STANCE=true` - 结构化立场识别
- `ENABLE_G2_CONFLICT_DIMENSIONS=true` - 冲突维度分析

---

### 2.3 G5 模式 (expert_diagnosis) - 专家诊断

**触发条件**: `fusion_mode="expert_diagnosis"`

**处理流程**:
```python
def _fuse_g5(request, group_id):
    1. 复用 G1 视角收集逻辑
    2. 调用 ExpertDiagnosisService.diagnose()
       - 风险评估 (risk_assessment)
       - 识别关键问题 (critical_issues)
       - 生成专家建议 (recommendations[])
       - 上线条件 (go_live_conditions)
       - 诊断摘要 (summary)
    3. G5 会过滤 offline worker（Registry-aware filtering）
    4. 支持 strict_participants 语义
    5. 返回专家诊断结果
```

**响应字段特点**:
```json
{
  "fusion_mode": "expert_diagnosis",
  "risk_assessment": {
    "overall": "high",
    "categories": { "security": "high", "database": "medium" }
  },
  "critical_issues": [
    {
      "issue": "SQL注入风险",
      "severity": "high",
      "domain": "security",
      "source": "security-expert"
    }
  ],
  "recommendations": [                // 复数，多个建议
    {
      "priority": "P0",
      "action": "修复SQL注入漏洞",
      "owner": "开发团队",
      "domain": "security"
    }
  ],
  "go_live_conditions": ["修复所有P0问题", "通过安全审计"],
  "summary": "存在安全隐患，建议修复后再上线",
  "conflicts": [],                    // G2 字段为空
  "alignment_points": []
}
```

**V2 Feature Flags**:
- `ENABLE_G5_STRUCTURED_RISK=true` - 结构化风险评估

---

## 3. 视角收集流程详解

### 3.1 参与者 ID 格式

**重要**: `participants[]` 必须使用 `profile_key` 格式：

```
格式: "{worker_id}:{profile_name}"

示例:
✅ "wrk_architect_001:default"
✅ "staff_334018:profile_v1"

❌ 错误: "wrk_architect_001"  (纯 worker_id 无法找到绑定)
```

### 3.2 收集流程

```python
def _collect_perspectives(request, group_id, driver_bot_id):
    perspectives = []
    warnings = []

    # Phase 5: 预检查参与者可用性
    for participant_id in request.participants:
        availability = availability_checker.check(participant_id)

        if not availability.is_available:
            # 添加警告
            warnings.append(f"participant {participant_id} is offline")

            # strict_participants 模式:
            if request.options.strict_participants:
                # 严格模式: 跳过，不创建 perspective
                continue
            else:
                # 兼容模式: 创建 status="skipped" 的 perspective
                perspectives.append(create_skipped_perspective(participant_id))
                continue

        # 正常收集视角
        context = PerspectiveContext(
            group_id=group_id,
            question=request.question,
            participant_id=participant_id,
            driver_bot_id=driver_bot_id,
            timeout_ms=request.options.timeout_ms
        )
        perspective = perspective_provider.collect(context)
        perspectives.append(perspective)

    return perspectives, warnings
```

---

## 4. PerspectiveProvider 实现

### 4.1 接口定义

```python
class PerspectiveProvider(Protocol):
    def collect(self, context: PerspectiveContext) -> Perspective:
        """收集单个 participant 的视角"""
        ...
```

### 4.2 LLMPerspectiveProvider（生产环境）

**文件**: `src/infra/providers/llm_perspective_provider.py`

**执行流程**:
```python
def collect(self, context):
    # Step 1: 解析 participant_id
    staff_id, profile_id = self._parse_participant_id("wrk_001:default")
    # → ("wrk_001", "default")

    # Step 2: 获取 Worker Profile（三层合并策略）
    profile = profile_source.get_profile(staff_id, profile_id)
    profile_content = extract_searchable_text_and_fragments(profile)

    # Step 3: 构建 Prompt
    system_prompt = PERSPECTIVE_SYSTEM_PROMPT
    user_prompt = PERSPECTIVE_USER_PROMPT_TEMPLATE.format(
        question=context.question,
        expert_id=context.participant_id,
        profile_content=profile_content[:3000]  # 限制长度
    )

    # Step 4: 调用 LLM
    request = LLMRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.3,
        max_tokens=4096
    )
    response = llm_gateway.generate(request)

    # Step 5: 解析为 Perspective
    return Perspective(
        participant_id=context.participant_id,
        participant_type="bot",
        role="consultant",
        summary=response.structured_data["summary"],
        confidence=response.structured_data["confidence"],
        key_points=response.structured_data["key_points"],
        concerns=response.structured_data["concerns"],
        evidence=response.structured_data["evidence"],
        status="completed"
    )
```

---

## 5. Worker Profile 来源（三层合并）

### 5.1 来源层级

**文件**: `src/infra/worker_profiles/sources/composite_worker_profile_source.py`

```
┌─────────────────────────────────────────────────────────┐
│           CompositeWorkerProfileSource                    │
│                    (合并策略)                            │
└────────────┬───────────────────────────┬────────────────┘
             │                           │
    ┌────────▼────────┐        ┌────────▼────────┐
    │  Registry Source │        │   API Source   │
    │  (最高优先级)     │        │  (内容补充)     │
    │                  │        │                │
    │ • Worker 基础信息│        │ • soul_md     │
    │ • responsibilities│       │ • active_skills│
    │ • capabilities   │        │ • context_     │
    │                  │         │   fragments   │
    └────────┬─────────┘        └────────┬────────┘
             │                          │
             └──────────┬───────────────┘
                        │
               合并规则: 如果 Registry 稀疏
               且 API 内容丰富 → 自动合并
```

### 5.2 合并策略

```python
def _merge_profiles(registry_profile, api_profile):
    # 判断内容丰富度
    registry_fragments = registry_profile.context_fragments or []
    api_fragments = api_profile.context_fragments or []

    # 条件 1: Registry 完全空 → 合并
    is_registry_sparse = len(registry_fragments) == 0

    # 条件 2: API 内容更丰富 → 合并
    api_has_richer_content = (
        api_fragment_content > registry_fragment_content or
        len(api_skills) > len(registry_skills)
    )

    if is_registry_sparse or api_has_richer_content:
        return merged_profile(
            registry_base + api_content
        )

    return registry_profile
```

### 5.3 Profile 来源配置

**文件**: `src/interfaces/api/dependencies/fusion_dependencies.py`

```python
def _get_profile_source():
    # 1. Registry 来源（从已注册 Worker 构建）
    registry_source = RegistryWorkerProfileSource(
        registry_store=registry_store,
        runtime_state_store=runtime_state_store,
        include_offline=False  # 只包含 online Worker
    )

    # 2. API 来源（从 worker_profile_contents 表）
    api_source = APIProfileSource()

    # 3. FILE 来源（文件系统，兼容历史）
    file_source = FileWorkerProfileSource()

    # 组合来源
    return CompositeWorkerProfileSource(
        registry_source=registry_source,
        api_source=api_source,
        file_source=file_source
    )
```

---

## 6. Prompt 示例

### 6.1 System Prompt

```text
你是一个"专业视角生成器"。

你的任务是基于给定的专家画像和问题，生成一个专业、有深度的视角。

你必须遵守以下规则：

1. 必须基于输入的专家画像内容生成视角，不得虚构专家未提及的技能或经验。
2. 生成的视角必须具有实质内容，包括：
   - summary: 针对问题的专业见解摘要
   - key_points: 核心观点列表（至少2个）
   - concerns: 主要顾虑列表（至少1个）
   - evidence: 支持观点的证据或依据
3. 视角应该体现专家的专业背景和决策风格。
4. 输出必须是严格的 JSON 格式。

置信度(confidence)指南：
- 0.9-1.0: 有充分的专业背景支持，观点明确
- 0.7-0.9: 有相关的专业背景，但需要更多信息确认
- 0.5-0.7: 专业背景不太相关，或信息不足
```

### 6.2 User Prompt Template

```text
请基于以下专家画像，生成专业视角 JSON。

[问题]
{question}

[专家标识]
{expert_id}

[专家画像内容]
{profile_content}

请输出一个严格 JSON 对象：
{
  "summary": "专业视角摘要（100-300字）",
  "confidence": 0.0-1.0之间的浮点数,
  "key_points": ["核心观点1", "核心观点2", ...],
  "concerns": ["主要顾虑1", "主要顾虑2", ...],
  "evidence": ["支持证据1", "支持证据2", ...]
}

要求：
- key_points 至少包含2个观点
- concerns 至少包含1个顾虑
- summary 必须体现专家的专业背景
- 不要输出任何 JSON 之外的文字
```

---

## 7. 依赖注入配置

### 7.1 Perspective Provider 选择

```python
def _get_perspective_provider():
    llm_enabled = os.environ.get("LLM_ENABLED", "").lower() == "true"

    if llm_enabled:
        # 生产环境: 使用真实 LLM
        return LLMPerspectiveProvider(
            gateway=llm_gateway,
            profile_source=profile_source
        )
    else:
        # 开发/测试: 使用假数据
        return StubPerspectiveProvider()
```

### 7.2 关键环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| `LLM_ENABLED` | 启用 LLM 生成视角 | `true` |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.example.com/v1` |
| `LLM_AUTH_TOKEN` | 认证 Token | `sk-...` |
| `LLM_DEFAULT_TIMEOUT_MS` | 默认超时 | `60000` (60秒) |
| `LLM_REASONING_TIMEOUT_MS` | 推理任务超时 | `90000` (90秒) |

---

## 8. Timeout 限制

| 模式 | 最大 timeout_ms | 说明 |
|------|----------------|------|
| G1 (agent) | 300000ms (5分钟) | 每个视角约50-60秒 |
| G2 (conflict_alignment) | 300000ms (5分钟) | 同 G1 |
| G5 (expert_diagnosis) | 600000ms (10分钟) | 诊断任务需要更长时间 |

**超时错误**:
```json
{
  "detail": [{
    "type": "value_error",
    "msg": "timeout_ms=360000 exceeds maximum allowed for G1/G2 (agent) mode (max=300000ms)"
  }]
}
```

---

## 9. 响应字段对比表

| 字段 | G1 (agent) | G2 (conflict_alignment) | G5 (expert_diagnosis) |
|------|------------|------------------------|----------------------|
| `fusion_mode` | "agent" | "conflict_alignment" | "expert_diagnosis" |
| `recommendation` | ✅ dict | ✅ dict | ✅ dict |
| `recommendations` | [] (空) | [] (空) | ✅ array |
| `conflicts` | [] | ✅ array | [] |
| `alignment_points` | [] | ✅ array | [] |
| `key_insights` | [] | ✅ array | [] |
| `conclusion` | null | ✅ object | null |
| `risk_assessment` | null | null | ✅ object |
| `critical_issues` | [] | [] | ✅ array |
| `go_live_conditions` | [] | [] | ✅ array |
| `summary` | null | null | ✅ string |
| `structured_risk` | null | null | ✅ object (需 Flag) |
| `structured_conflict_analysis` | null | ✅ object (需 Flag) | null |

---

## 10. V2 Feature Flags

| Flag | 说明 | 影响 |
|------|------|------|
| `ENABLE_G2_STRUCTURED_STANCE` | G2 结构化立场识别 | `structured_conflict_analysis.stance_analysis` |
| `ENABLE_G2_CONFLICT_DIMENSIONS` | G2 冲突维度分析 | `structured_conflict_analysis.conflict_dimensions` |
| `ENABLE_G5_STRUCTURED_RISK` | G5 结构化风险评估 | `structured_risk` 字段 |
| `ENABLE_HYBRID_RETRIEVAL` | 混合检索 | 影响参与者推荐 |
| `ENABLE_VECTOR_AWARE_RECOMMENDATION` | 向量感知推荐 | 使用向量相似度匹配 |

---

## 11. 完整调用链

```
HTTP Request
    ↓
POST /api/v1/groups/{group_id}/fuse
    ↓
Route Handler (fusion_routes.py)
    - 校验 group_id 格式
    - 解析 FusionRequest
    ↓
GroupFusionService.fuse(request, group_id)
    - 根据 fusion_mode 分发
    ↓
    ├─ G1 → _fuse_g1()
    ├─ G2 → _fuse_g2() → ConflictAlignmentService.align()
    └─ G5 → _fuse_g5() → ExpertDiagnosisService.diagnose()
        ↓
    _collect_perspectives()
        ↓ 对每个 participant
        PerspectiveProvider.collect(PerspectiveContext)
            ↓
        LLMPerspectiveProvider.collect()
            - _parse_participant_id() → (staff_id, profile_id)
            - _get_profile_content()
                ↓ CompositeWorkerProfileSource.get_profile()
                    - RegistryWorkerProfileSource (基础信息)
                    - APIProfileSource (soul_md 内容)
                    - FileWorkerProfileSource (文件补充)
                    - _merge_profiles() (合并策略)
            - 构建 Prompt
            - LLMGatewayService.generate()
            - _parse_llm_response() → Perspective
        ↓
    返回 perspectives[]
    ↓
    [模式特定处理]
        G1: _generate_llm_recommendation() / _synthesize_recommendation()
        G2: ConflictAlignmentService 分析冲突和对齐点
        G5: ExpertDiagnosisService 风险评估和建议
    ↓
FusionResult
    ↓
转换为 FuseResponse
    ↓
HTTP Response (JSON)
```

---

## 12. 关键文件位置

| 文件 | 职责 |
|------|------|
| `src/interfaces/api/fusion_routes.py` | HTTP 路由、参数校验、响应转换 |
| `src/application/services/group_fusion_service.py` | 融合服务主逻辑、三种模式分发 |
| `src/infra/providers/llm_perspective_provider.py` | LLM 视角生成实现 |
| `src/infra/providers/stub_perspective_provider.py` | 测试用 Stub 实现 |
| `src/infra/worker_profiles/sources/composite_worker_profile_source.py` | Profile 三层合并来源 |
| `src/interfaces/api/dependencies/fusion_dependencies.py` | 依赖注入配置 |
| `src/application/services/conflict_alignment_service.py` | G2 冲突对齐逻辑 |
| `src/application/services/expert_diagnosis_service.py` | G5 专家诊断逻辑 |

---

*文档生成时间: 2026-04-08*
*基于代码版本: master 分支*
