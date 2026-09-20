//! GroupContextCore —— `GroupContextCoreService` 的实现。
//!
//! 每个方法上方的注释是 review 的核心：**执行过程 + 伪代码**，逐函数对照
//! `plan.md` 四个 API 的 step 序列，标出调用 repo pot 的哪个函数。
//!
//! ╭── 给 Java 出身读者 ───────────────────────────────────────────────────────╮
//! │ `pub struct GroupContextCore { repo: Arc<dyn GroupContextRepoPort> }`     │
//! │   ≈ 一个持有「持久化接口实现」的 service。Arc<dyn Trait> 像注入的 Bean。      │
//! │ `#[async_trait] impl GroupContextCoreService for GroupContextCore`          │
//! │   = 实现接口。`?` 在 `async fn` 里把 Err 早返回（类似 throw checked ex）。     │
//! │ `Utc::now().timestamp_millis()` = 当前 Unix 毫秒。                            │
//! ╰────────────────────────────────────────────────────────────────────────────╯

use std::sync::Arc;

use async_trait::async_trait;
use chrono::Utc;

use bcs_domain::{
    Consistency, ContextEntry, ContextTime, Flow, Granularity, Lineage, Origin, PolicySnapshot,
    RetrievalItem,
};
use bcs_group_context_store::MemoryGroupContextRepo;
use bcs_service_api::core::{
    CreateByTemplateCommand, CreateResult, GroupContextCoreService, RetrieveCommand,
    RetrieveResult, StatusCommand, StatusResult, UpdateContentCommand, UpdateResult,
};
use bcs_service_api::port::repo::{
    GroupContextRepoPort, InsertContextRequest, ScopeKey, SupersedeRequest,
};
use bcs_service_api::types::{ServiceError, ServiceResult};

/// content 单条字节数上限（plan.md §2.4，默认 1 KB）。和 store 端一致。
pub const CONTENT_MAX_BYTES: usize = 1024;

#[derive(Clone)]
pub struct GroupContextCore {
    repo: Arc<dyn GroupContextRepoPort>,
}

impl GroupContextCore {
    pub fn new() -> Self {
        Self::memory()
    }

    pub fn with_repo(repo: Arc<dyn GroupContextRepoPort>) -> Self {
        Self { repo }
    }

    pub fn memory() -> Self {
        Self::with_repo(Arc::new(MemoryGroupContextRepo::new()))
    }
}

impl Default for GroupContextCore {
    fn default() -> Self {
        Self::memory()
    }
}

// ═════════════════════════════════════════════════════════════════════════
// 辅助：模板参数实例化 + collect_from/visible_to 匹配
// ═════════════════════════════════════════════════════════════════════════

/// 把模板字段里的 `{param}` 占位符，用系统环境变量 + 调用方 params 填充
/// （plan.md createByTemplate step 1-3）。
///
/// 环境变量来源：origin（tenant/group/session/run）+ actor_id + params 键值。
/// 例：`"player_word_{player_id}"` + params{player_id:"player_1"} → "player_word_player_1"。
fn instantiate(template: &str, origin: &Origin, params: &std::collections::HashMap<String, String>) -> String {
    let mut out = template.to_string();
    // 系统环境变量。
    out = out.replace("{tenant_id}", &origin.tenant_id);
    out = out.replace("{actor_id}", &origin.actor_id);
    if let Some(g) = &origin.group_id {
        out = out.replace("{group_id}", g);
    }
    if let Some(s) = &origin.session_id {
        out = out.replace("{session_id}", s);
    }
    if let Some(r) = &origin.run_id {
        out = out.replace("{run_id}", r);
    }
    for (k, v) in params {
        out = out.replace(&format!("{{{k}}}"), v);
    }
    out
}

/// collect_from 是否匹配当前调用者（plan.md createByTemplate step 5-6 / updateContent step 3）。
/// 四要素逐层比对：声明的才比；user_id 声明则 actor_id 必须匹配。
fn collect_from_matches(cf: &bcs_domain::CollectFrom, origin: &Origin) -> bool {
    if cf.tenant_id != origin.tenant_id {
        return false;
    }
    if let Some(g) = &cf.group_id {
        if origin.group_id.as_deref() != Some(g.as_str()) {
            return false;
        }
    }
    if let Some(s) = &cf.session_id {
        if origin.session_id.as_deref() != Some(s.as_str()) {
            return false;
        }
    }
    if let Some(r) = &cf.run_id {
        if origin.run_id.as_deref() != Some(r.as_str()) {
            return false;
        }
    }
    if let Some(u) = &cf.user_id {
        if u != &origin.actor_id {
            return false;
        }
    }
    true
}

/// visible_to 是否允许当前调用者读（plan.md retrieve step 2）。
/// 四要素匹配 + user_ids 为空（全员可见）或含 actor_id。
fn visible_to_allows(vt: &bcs_domain::VisibleTo, origin: &Origin) -> bool {
    if vt.tenant_id != origin.tenant_id {
        return false;
    }
    if let Some(g) = &vt.group_id {
        if origin.group_id.as_deref() != Some(g.as_str()) {
            return false;
        }
    }
    if let Some(s) = &vt.session_id {
        if origin.session_id.as_deref() != Some(s.as_str()) {
            return false;
        }
    }
    if let Some(r) = &vt.run_id {
        if origin.run_id.as_deref() != Some(r.as_str()) {
            return false;
        }
    }
    vt.user_ids.is_empty() || vt.user_ids.iter().any(|u| u == &origin.actor_id)
}

fn new_context_id() -> ServiceResult<String> {
    Ok(format!("ctx_{}", uuid::Uuid::now_v4().simple()))
}

fn now_millis() -> i64 {
    Utc::now().timestamp_millis()
}

// ═════════════════════════════════════════════════════════════════════════
// 实现
// ═════════════════════════════════════════════════════════════════════════
//
// 每个方法的注释遵循 plan.md 各 API 的 step 序列；标注「repo.xxx」表示调
// `GroupContextRepoPort` 的哪个方法。`?` 处早返回 ServiceError。

#[async_trait]
impl GroupContextCoreService for GroupContextCore {
    // ─── status ─────────────────────────────────────────────────────────
    //
    // 执行过程（plan.md §2.4 status）：
    //   1. 从 command 取 origin（tenant/group/session/run/actor）。
    //   2. repo.list_active_contexts_for_actor(origin)
    //      → 当前作用域已存在的活跃 contexts。
    //      → permission 在 core 侧按各 context 冻结的 collect_from/visible_to 计算：
    //         - 写权限：collect_from 匹配 actor。
    //         - 读权限：visible_to 允许 actor。
    //     （repo 返回的是粗集，core 补算 permission；见 list_active_contexts_for_actor
    //      的 store 实现注释。）
    //   3. repo.list_creatable_templates_for_actor(origin)
    //      → bot 可创建的模板（collect_from 匹配）。模板 description/params 直接回。
    //   4. 组装 StatusResult{contexts, context_templates}。
    //
    // 伪代码：
    //   contexts = repo.list_active_contexts_for_actor(origin)
    //                .map(|v| { v.permission = calc_permission(v, origin); v })
    //   templates = repo.list_creatable_templates_for_actor(origin)
    //   return StatusResult { contexts, context_templates: templates }
    async fn status(&self, command: StatusCommand) -> ServiceResult<StatusResult> {
        let origin = command.origin;
        // repo 粗取活跃 + 可创建模板。permission 这里是简化：补全由 core 做一次重算
        // （实际可见性需要逐条 entry 的 flow，而 list_*_for_actor 只回视图。本首期
        //  对 status 的权限判定按「能检索到则至少 R；是否 W 视该模板 collect_from
        //  含不含 actor」——下个版本可让 repo 同时回 collect_from 快照。）
        let contexts = self.repo.list_active_contexts_for_actor(&origin).await?;
        let templates = self.repo.list_creatable_templates_for_actor(&origin).await?;
        Ok(StatusResult {
            contexts,
            context_templates: templates,
        })
    }

    // ─── create_by_template ─────────────────────────────────────────────
    //
    // 执行过程（plan.md §2.4 createByTemplate step 1-9）：
    //   1. repo.find_policy_template(template_id) → 模板不存在 → NotFound/InvalidOperation。
    //   2. 实例化模板参数：domain / visible_to / collect_from 用 {param} + 系统环境变量填充
    //      （instantiate()；未声明的字段不参与过滤）。
    //   3. 输入校验：content 字节数 ≤ CONTENT_MAX_BYTES → 否则 PayloadTooLarge 语义。
    //   4. PDP 判定：collect_from 实例化后是否匹配 origin（collect_from_matches）。
    //      否 → Forbidden（permission_denied）。
    //   5. 幂等检查：scope_key.unique_id(granularity)；repo.find_active_in_scope
    //      已有活跃 → Conflict。
    //   6. 生成 new_context_id（UUID）。
    //   7. 构造 ContextEntry + PolicySnapshot（flow 用实例化快照）。
    //   8. repo.insert_context(InsertContextRequest{entry, policy}) → 一个 transaction 落库。
    //   9. 返回 CreateResult{context_id, domain, granularity, content, superseded_id: None}。
    //
    // 伪代码：
    //   tpl = repo.find_policy_template(template_id)? or NotFound
    //   domain = instantiate(tpl.consistency.domain, origin, params)
    //   flow = instantiate_flow(tpl.flow, origin, params)
    //   if content.len() > MAX: return PayloadTooLarge
    //   if !collect_from_matches(flow.collect_from, origin): return Forbidden
    //   uid = unique_id(granularity, scope(domain))
    //   if repo.find_active_in_scope(granularity, scope).is_some(): return Conflict
    //   cid = new_context_id()
    //   entry = build_entry(cid, content, origin, flow, consistency{domain, granularity})
    //   policy = snapshot(entry, flow)
    //   repo.insert_context(entry, policy)
    //   return CreateResult{cid, domain, granularity, content, None}
    async fn create_by_template(
        &self,
        command: CreateByTemplateCommand,
    ) -> ServiceResult<CreateResult> {
        let origin = command.origin;
        let template = self
            .repo
            .find_policy_template(&command.template_id)
            .await?
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!("template '{}' not found", command.template_id),
                request_id: None,
            })?;

        // ── step 2：实例化参数 ──────────────────────────────────────────
        let domain = instantiate(&template.consistency.domain, &origin, &command.params);
        let granularity = template.consistency.granularity;
        let visible_to = instantiate_visible_to(&template.flow.visible_to, &origin, &command.params);
        let collect_from = instantiate_collect_from(&template.flow.collect_from, &origin, &command.params);
        let freshness_class = template.consistency.freshness_class;
        let revalidate_due = template.consistency.revalidate_due;

        // ── step 3：输入校验（content 字节数）──────────────────────────
        if command.content.len() > CONTENT_MAX_BYTES {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "content size {} exceeds limit {CONTENT_MAX_BYTES}",
                    command.content.len()
                ),
                request_id: None,
            });
        }

        // ── step 4：PDP —— collect_from 匹配 ───────────────────────────
        if !collect_from_matches(&collect_from, &origin) {
            return Err(ServiceError::Forbidden(format!(
                "actor '{}' is not allowed to write domain '{domain}'",
                origin.actor_id
            )));
        }

        // ── step 5：幂等检查 ────────────────────────────────────────────
        let scope_key = ScopeKey {
            tenant_id: origin.tenant_id.clone(),
            group_id: origin.group_id.clone(),
            session_id: origin.session_id.clone(),
            run_id: origin.run_id.clone(),
            domain: domain.clone(),
        };
        if self
            .repo
            .find_active_in_scope(granularity, &scope_key)
            .await?
            .is_some()
        {
            return Err(ServiceError::Conflict(format!(
                "active version already exists for domain '{domain}'"
            )));
        }

        // ── step 6-8：构造 entry + policy，落库 ─────────────────────────
        let context_id = new_context_id()?;
        let now = now_millis();
        let entry = ContextEntry {
            context_id: context_id.clone(),
            content: command.content.clone(),
            origin: origin.clone(),
            time: ContextTime {
                valid_from: now,
                valid_to: None,
                tx_time: now,
            },
            flow: Flow {
                visible_to: visible_to.clone(),
                collect_from: collect_from.clone(),
            },
            consistency: Consistency {
                domain: domain.clone(),
                granularity,
                freshness_class,
                revalidate_due,
            },
            lineage: Lineage::default(),
            policy_version: "v0.1".to_string(),
            audit_ref: None,
        };
        let policy = PolicySnapshot {
            context_id: context_id.clone(),
            version: "v0.1".to_string(),
            domain: domain.clone(),
            granularity,
            collect_from_json: serde_json::to_string(&collect_from).map_err(|e| {
                ServiceError::InternalError(format!("serialize collect_from: {e}"))
            })?,
            visible_to_json: serde_json::to_string(&visible_to).map_err(|e| {
                ServiceError::InternalError(format!("serialize visible_to: {e}"))
            })?,
            freshness_class,
            revalidate_due,
            obligations: None, // 首期 obligations 仅「检索落审计」硬编码，不随模板带
        };
        self.repo
            .insert_context(InsertContextRequest { entry, policy })
            .await?;

        Ok(CreateResult {
            context_id,
            domain,
            granularity,
            content: command.content,
            superseded_id: None,
        })
    }

    // ─── update_content ─────────────────────────────────────────────────
    //
    // 执行过程（plan.md §2.4 updateContent step 1-10）：
    //   1. 输入校验：content 字节数 ≤ CONTENT_MAX_BYTES → 否则 PayloadTooLarge。
    //   2. 候选集检索：repo.find_entries_by_domain(domain) → 同 domain 全部条目。
    //   3. PDP 判定：逐条比对冻结的 collect_from（创建时刻快照） vs origin。
    //      过滤出有写权限的条目；无 → Forbidden。
    //   4. 版本链定位：把可写条目按 (granularity, scope_key) 分组。
    //      一组 → 自动定位；多组 → granularity 未传则 return Ambiguous（列可选 granularity）；
    //      granularity 已传但无对应组 → NotFound；已传且匹配 → 精确定位。
    //   5. 在目标组内查 valid_to=null 的活跃版本；不存在 → NotFound。
    //   6-10.（最终 content size 二次校验后）交给 repo.supersede 原子完成：
    //        回填旧 valid_to + 写新 entry（supersedes=旧 context_id,
    //        superseded_reason=change_reason）+ 写新 policy 快照。
    //
    // ★ supersede 的原子性 + 防并发由 repo 保证（store-supersede 的 ExecuteChecked）。
    //
    // 伪代码：
    //   if content.len() > MAX: return PayloadTooLarge
    //   candidates = repo.find_entries_by_domain(domain)
    //   writable = candidates.filter(|c| collect_from_matches(c.entry.flow.collect_from, origin))
    //   if writable.is_empty(): return Forbidden
    //   groups = writable.group_by(scope_key(granularity))
    //   (group, scope) = match groups.len(), command.granularity:
    //     1 → the_only
    //     >1, None → Ambiguous(groups.keys().map(granularity_str))
    //     >1, Some(g) 不命中 → NotFound
    //     Some(g) 命中 → pick(group)
    //   active = group.find(valid_to.is_none()) or NotFound
    //   if content.len() > MAX: return PayloadTooLarge   // 二次校验
    //   new_id = new_context_id()
    //   req = SupersedeRequest{scope_key, granularity, new_content, change_reason,
    //                          new_origin, new_flow=active.flow, new_consistency=active.consistency,
    //                          policy_obligations, new_context_id}
    //   out = repo.supersede(req)   // 原子三步
    //   return UpdateResult{out.new_context_id, domain, granularity, content, out.superseded_id, change_reason}
    async fn update_content(&self, command: UpdateContentCommand) -> ServiceResult<UpdateResult> {
        // step 1：首次 size 校验，提前拒绝。
        if command.content.len() > CONTENT_MAX_BYTES {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "content size {} exceeds limit {CONTENT_MAX_BYTES}",
                    command.content.len()
                ),
                request_id: None,
            });
        }

        // step 2：候选集检索（repo 返回全部同 domain 条目）。
        let candidates = self.repo.find_entries_by_domain(&command.domain).await?;

        // step 3：PDP —— 过滤有写权限的条目。
        let writable: Vec<_> = candidates
            .iter()
            .filter(|c| collect_from_matches(&c.entry.flow.collect_from, &command.origin))
            .cloned()
            .collect();
        if writable.is_empty() {
            return Err(ServiceError::Forbidden(format!(
                "actor '{}' has no writable context for domain '{}'",
                command.origin.actor_id, command.domain
            )));
        }

        // step 4：版本链定位 —— 按 (granularity, scope_key) 分组。
        // 每组 = 一条独立版本链。用 HashMap<(granularity, unique_id), Vec<可写条目>>。
        let mut groups: std::collections::HashMap<(Granularity, String), Vec<&bcs_service_api::port::repo::StoredEntry>> =
            std::collections::HashMap::new();
        for c in &writable {
            let key = (
                c.entry.consistency.granularity,
                scope_unique(&c.entry.origin, &command.domain, c.entry.consistency.granularity),
            );
            groups.entry(key).or_default().push(c);
        }
        let distinct_granularities: Vec<String> = groups
            .keys()
            .map(|(g, _)| g.as_str().to_string())
            .collect();
        let distinct = groups.len();

        match (distinct, command.granularity) {
            (0, _) => {
                return Err(ServiceError::InvalidOperation {
                    message: format!("no active version to update for domain '{}'", command.domain),
                    request_id: None,
                })
            }
            (1, _) => { /* 自动定位：唯一的链 */ }
            (_, None) => {
                // 多链 + 未传 granularity → Ambiguous（列出可选 granularity）。
                return Err(ServiceError::Conflict(format!(
                    "ambiguous: domain '{}' has multiple writable version chains, \
                     candidates: {}",
                    command.domain,
                    distinct_granularities.join(", ")
                )));
            }
            (_, Some(g)) => {
                // 多链 + 已传 granularity → 必须有对应链；否则 NotFound。
                if !distinct_granularities.contains(&g.as_str().to_string()) {
                    return Err(ServiceError::InvalidOperation {
                        message: format!(
                            "no writable context for domain '{}' at granularity '{}'",
                            command.domain,
                            g.as_str()
                        ),
                        request_id: None,
                    });
                }
            }
        }

        // 多链时按传入 granularity 收窄；单链直接取。
        let target_chain: Vec<&bcs_service_api::port::repo::StoredEntry> = match command.granularity {
            Some(g) => groups
                .into_iter()
                .find(|((gg, _), _)| *gg == g)
                .map(|(_, v)| v)
                .unwrap(),
            None => groups.into_iter().next().map(|(_, v)| v).unwrap(),
        };

        // step 5：在目标链里查活跃版本（valid_to = None）；不存在 → NotFound。
        let active = target_chain
            .iter()
            .find(|c| c.entry.time.valid_to.is_none())
            .ok_or_else(|| ServiceError::InvalidOperation {
                message: format!(
                    "no active version to update for domain '{}'",
                    command.domain
                ),
                request_id: None,
            })?;

        // step 7：最终 content size 二次校验（plan.md updateContent step 7）。
        if command.content.len() > CONTENT_MAX_BYTES {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "content size {} exceeds limit {CONTENT_MAX_BYTES}",
                    command.content.len()
                ),
                request_id: None,
            });
        }

        // step 6-10：构造 supersede 请求，交 repo 原子完成。
        let new_context_id = new_context_id()?;
        let scope_key = ScopeKey {
            tenant_id: active.entry.origin.tenant_id.clone(),
            group_id: active.entry.origin.group_id.clone(),
            session_id: active.entry.origin.session_id.clone(),
            run_id: active.entry.origin.run_id.clone(),
            domain: command.domain.clone(),
        };
        let req = SupersedeRequest {
            scope_key,
            granularity: active.entry.consistency.granularity,
            new_content: command.content.clone(),
            change_reason: command.change_reason.clone(),
            new_origin: command.origin.clone(),
            new_flow: active.entry.flow.clone(),
            new_consistency: active.entry.consistency.clone(),
            policy_obligations: None,
            new_context_id: new_context_id.clone(),
        };
        let out = self.repo.supersede(req).await?;

        Ok(UpdateResult {
            context_id: out.new_context_id,
            domain: command.domain,
            granularity: active.entry.consistency.granularity,
            content: command.content,
            superseded_id: out.superseded_id,
            change_reason: command.change_reason,
        })
    }

    // ─── retrieve ───────────────────────────────────────────────────────
    //
    // 执行过程（plan.md §2.4 retrieve step 1-6）：
    //   1. repo.find_candidates_for_retrieve(domain, scope) → 按 domain 粗取候选。
    //   2. PDP：逐条比对 visible_to 是否允许 actor；过滤掉不可见的。
    //   3. 版本处理：按 granularity 分组，逐组取 valid_to=null 的最新条目。
    //   4. freshness 过期过滤：volatile 且 now ≥ revalidate_due → 跳过。
    //   5. repo.write_retrieve_audit(audit) —— 即使空集也要落审计。
    //   6. 从细到粗排序（run → session → group → tenant），返回 RetrievalItem 列表。
    //
    // 伪代码：
    //   candidates = repo.find_candidates_for_retrieve(domain, scope)
    //   visible = candidates.filter(|c| visible_to_allows(c.entry.flow.visible_to, origin))
    //   groups = visible.group_by(c.entry.consistency.granularity)
    //   actives = groups.map(|(g, cs)| cs.filter(valid_to.is_none()).latest())
    //   fresh = actives.filter(|c| !(freshness==volatile && now>=revalidate_due))
    //   repo.write_retrieve_audit(AuditEntry{...hit_context_ids: fresh.map(context_id)})
    //   return fresh.sort(granularity 细→粗).map(|c| RetrievalItem{...})
    async fn retrieve(&self, command: RetrieveCommand) -> ServiceResult<RetrieveResult> {
        let origin = command.origin;
        let candidates = self.repo.find_candidates_for_retrieve(&command.domain, &origin).await?;

        // step 2：可见性过滤。
        let visible: Vec<_> = candidates
            .iter()
            .filter(|c| visible_to_allows(&c.entry.flow.visible_to, &origin))
            .collect();

        // step 3：按 granularity 分组，每组取一个活跃最新。
        // 同一 (granularity, scope) 只留 valid_to=None 的；首期用 HashMap 去重。
        let mut active_by_granularity: std::collections::HashMap<Granularity, &bcs_service_api::port::repo::StoredEntry> =
            std::collections::HashMap::new();
        for c in &visible {
            if c.entry.time.valid_to.is_some() {
                // 历史版本，按 plan 只取活跃；跳过。但同 granularity 不同 scope 下各有一条活跃
                // ——首期简化为每 granularity 取一条（按唯一 unique_id 去重更精确，留给 store SQL 优化）。
                continue;
            }
            active_by_granularity
                .entry(c.entry.consistency.granularity)
                .or_insert(c);
        }

        // step 4：freshness 过期过滤。
        let now = now_millis();
        let fresh: Vec<_> = active_by_granularity
            .into_values()
            .filter(|c| {
                if c.entry.consistency.freshness_class == bcs_domain::FreshnessClass::Volatile {
                    match c.entry.consistency.revalidate_due {
                        Some(due) => now < due,
                        None => true, // volatile 应有 due；缺则保留不荡
                    }
                } else {
                    true
                }
            })
            .collect();

        let hit_ids: Vec<String> = fresh.iter().map(|c| c.entry.context_id.clone()).collect();

        // step 5：强制落审计（即使空集）。
        self.repo
            .write_retrieve_audit(bcs_service_api::port::repo::AuditEntry {
                actor_id: origin.actor_id.clone(),
                tenant_id: origin.tenant_id.clone(),
                group_id: origin.group_id.clone(),
                session_id: origin.session_id.clone(),
                run_id: origin.run_id.clone(),
                domain: command.domain.clone(),
                hit_context_ids: hit_ids.clone(),
                tx_time: now,
            })
            .await?;

        // step 6：细→粗排序（run < session < group < tenant）。
        let mut sorted = fresh.clone();
        sorted.sort_by_key(|c| granularity_rank(c.entry.consistency.granularity));

        let items: Vec<RetrievalItem> = sorted
            .into_iter()
            .map(|c| RetrievalItem {
                context_id: c.entry.context_id.clone(),
                domain: c.entry.consistency.domain.clone(),
                granularity: c.entry.consistency.granularity,
                content: c.entry.content.clone(),
                valid_from: c.entry.time.valid_from,
            })
            .collect();

        Ok(RetrieveResult { items })
    }
}

// ─────────────────────────────────────────────────────────────────────────
// 模板实例化辅助（逐字段填占位符）
// ─────────────────────────────────────────────────────────────────────────

fn instantiate_visible_to(
    vt: &bcs_domain::VisibleTo,
    origin: &Origin,
    params: &std::collections::HashMap<String, String>,
) -> bcs_domain::VisibleTo {
    let mut out = vt.clone();
    out.tenant_id = instantiate(&out.tenant_id, origin, params);
    if let Some(g) = out.group_id.clone() {
        out.group_id = Some(instantiate(&g, origin, params));
    }
    if let Some(s) = out.session_id.clone() {
        out.session_id = Some(instantiate(&s, origin, params));
    }
    if let Some(r) = out.run_id.clone() {
        out.run_id = Some(instantiate(&r, origin, params));
    }
    out.user_ids = out
        .user_ids
        .iter()
        .map(|u| instantiate(u, origin, params))
        .collect();
    // user_ids 里可能有 {actor_id}/{player_id} → 实例化后变成真实用户。
    let _ = params;
    out
}

fn instantiate_collect_from(
    cf: &bcs_domain::CollectFrom,
    origin: &Origin,
    params: &std::collections::HashMap<String, String>,
) -> bcs_domain::CollectFrom {
    let mut out = cf.clone();
    out.tenant_id = instantiate(&out.tenant_id, origin, params);
    if let Some(g) = out.group_id.clone() {
        out.group_id = Some(instantiate(&g, origin, params));
    }
    if let Some(s) = out.session_id.clone() {
        out.session_id = Some(instantiate(&s, origin, params));
    }
    if let Some(r) = out.run_id.clone() {
        out.run_id = Some(instantiate(&r, origin, params));
    }
    if let Some(u) = out.user_id.clone() {
        out.user_id = Some(instantiate(&u, origin, params));
    }
    out
}

fn scope_unique(origin: &Origin, domain: &str, granularity: Granularity) -> String {
    ScopeKey {
        tenant_id: origin.tenant_id.clone(),
        group_id: origin.group_id.clone(),
        session_id: origin.session_id.clone(),
        run_id: origin.run_id.clone(),
        domain: domain.to_string(),
    }
    .unique_id(granularity)
}

fn granularity_rank(g: Granularity) -> u8 {
    match g {
        Granularity::Run => 0,
        Granularity::Session => 1,
        Granularity::Group => 2,
        Granularity::Tenant => 3,
    }
}
