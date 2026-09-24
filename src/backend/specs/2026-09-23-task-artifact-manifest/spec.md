# Task Artifact(任务产物 manifest)设计 spec

日期:2026-09-23
权威源:语雀《产物领域对象》(https://yuque.antfin.com/mad/enxdbg/gfap3r7tgc2aeptr)
实现范围拍板:阶段一(双写)+ 阶段二(读侧 Descriptor 切换);阶段三(停写老 output)不做。

## 1. 不变量(领域层强制)

- **I1 manifest**:Artifact 承载业务身份(`ArtifactKind`,闭合 StrEnum:NODE_RESULT / GRAPH_ROLLUP / INTERNAL_CONTROL)、作用域(`ArtifactScope{task_id,node_id,attempt}`)、内容、血缘(`ArtifactLineage`)、审计;**不承载**文件字节/临时 URL/Token/对象存储 key。
- **I2 tagged union**:content 为 kind-tagged 互斥分支 — Text{text,media_type} / File{resource_id,file_name,media_type,size_bytes?,sha256?};持久化为 `{"kind": ...}` dict;`from_content_dict` 遇未知 kind 抛 `TaskArtifactContentError`。**不做 payload+storage_ref 平铺**。
- **I3 双轴**:`kind`(业务)⊥ `media_type`(MIME)。
- **I4 版本分治**:`scope.attempt`(harness_retries 口径)/ `lineage.supersedes`(内容修订链)/ 派生 `lineage.derived_from`;**无单一 version 字段**。
- **I5 不可变**:内容变化 → 新 artifact_id + supersedes;repository 无 update 主字段方法;同 attempt 重试隔离(不覆盖上一 attempt)。
- **I6 File 就绪闸**:仅 status==READY 的 `ac_session_resource`(sr_ 前缀 resource_id)可发布为 File 分支;未就绪/缺失 → 拒该候选(WARNING,不吞整批)。
- **I7 双闸幂等**:fire 时点 = graph mutation 持久化**成功之后**(`_mutate_with_version_retry` 返回后 → 版本重放不重复 fire);DB `uk (task_id,node_id,attempt,content_hash)` dedupe 兜底。

## 2. 表 `task_artifact`(PR2)

uk: (artifact_id)、(task_id,node_id,attempt,content_hash)[dedupe];idx: (task_id,node_id,attempt,created_at)、(task_id)。content MEDIUMTEXT;created_at bigint(毫秒);gmt_create/gmt_modified(新表无历史包袱);无 FK(独立实体,trajectory 先例)。

## 3. 写侧 = fold 单点双写(PR3)

- seam:`TaskGraphService` 节点 fold(:678)与图级 fold(:787),fire 于版本重试成功后;`artifact_service: TaskArtifactServiceProtocol | None` 构造器可选注入(None=no-op+INFO,对齐 task_context_service 先例)。
- 分派(service 内单点,覆盖全部 7 类写源):扫描 fold 后 output — `sr_` 引用/sr_ reference dict → File 分支(READY 校验);其余 → Text;kind 按来源:NODE_RESULT / GRAPH_ROLLUP(图级,node=root,attempt=loop_round)/ INTERNAL_CONTROL(dispatcher skipped/notify/静态 mock)。
- prompt 协议(prompt_formatter.py):要求 bot 产出文件先经会话文件链路上传,回投以 `{resource_id,file_name,size_bytes}` 引用。

## 4. 读侧 Descriptor(PR4)

- DTO `ArtifactDescriptor` 进节点 DTO/轨迹末位事件/DoneOutput.artifacts;`is_primary` = 该 node `latest_for_node`;Text 投影可截断、File 仅元数据;下载经既有会话文件端点,无新端点、无 admission/authorization 登记。
- RuntimeInfo.output_artifact_ids / TrajectoryEvent.artifacts = **读时富化**(样板 session_msgs:不落库、不进 timeline 指纹),不做 extend_props 回写(避免 fold 幂等/版本干扰)。
- `_unwrap_node_output` 与单键兼容**不动**;get_task_context 三条件判定不动;prompt 执行注入不切换(child_outputs/blackboard/done_children 保持 M2)。

## 5. 决策偏离记录(与语雀设计稿的差异)

| 偏离 | 理由 |
|---|---|
| 双写失败:集中化模式上抛 `TaskArtifactPublishError`;**relay 模式降 WARNING**(稿:一律上抛) | relay successor immutable — 上抛后的重试会被不可变保护拒绝形成残局(Q1 探索证实);静默降级保留轨迹可见性 |
| I6 拒绝路径不上抛只拒候选(稿:文件须 READY 才发布一致) | 候选级粒度拒绝,不阻塞同批 Text 产物 |
| 文本无上限/auto 转文件 | content MEDIUMTEXT;触发场景未出现(稿 §15.4),列 M2 |
| artifact_kind = 闭合枚举(稿 §15.3 开放问题) | 全仓库 ReasonCatalog 等治理先例;防脏值进领域 |
| RuntimeInfo ids 落库 vs 读时富化 → **读时富化** | fold 幂等/版本比较零干扰;session_msgs 先例 |

## 6. M2 后置清单

- `Structured` / `Collection` content 分支 + SchemaRef
- `supersedes` 之外的 `derived_from` 实际使用(文本提取派生产物)
- prompt 执行注入切换 artifact、独立长期归档 Blob(session 删除后存活)、提取/预览 Projector、文本 inline 上限与自动转文件、compaction(折叠 supersedes 长链)
- 独立 /artifacts 查询端点(现阶段嵌入 dashboard/trajectory)

## 7. 测试矩阵

五层:纯领域(roundtrip/未知 kind 抛错/id 工厂)、仓储(SQLite:create_or_get 幂等/dedupe/latest_for_node/supersedes 链)、服务(fold×N 幂等/演进链/attempt 隔离/file READY/未接线 no-op/上抛与 relay 降级)、HTTP(DTO/HTML/openapi 透传)、守卫(1000 行 cap/_OWNER_CLASSES/ENVELOPE_ERRORS)。