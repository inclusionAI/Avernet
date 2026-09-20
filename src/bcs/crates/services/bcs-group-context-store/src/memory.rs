//! In-memory group context repository，测试与单机开发用。
//!
//! ⚠️ 本文件首期为「最小可用骨架」，不是 SQLite 版的等价实现：
//!   - 用 `RwLock<HashMap<unique_id, Vec<ContextEntry>>>` 存版本链；
//!   - 模板表、审计表用独立 HashMap；
//!   - 不模拟 SQL 行锁/事务隔离——并发安全测试请走 SQLite 版（DbPlugin::transaction）。
//! 复杂的 supersede 原子性在 SQLite 版由 transaction 保证；这里用同步 `write` 锁
//! 串行化（单机内存里足够，因为 RwLock 的写锁本身互斥）。

use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use tokio::sync::RwLock;

use bcs_domain::{
    ContextEntry, ContextView, Granularity, PolicyTemplate, TemplateView,
};
use bcs_service_api::port::repo::{
    AuditEntry, GroupContextRepoPort, InsertContextRequest, ScopeKey, StoredEntry,
    SupersedeOutcome, SupersedeRequest,
};
use bcs_service_api::types::{ServiceError, ServiceResult};

use crate::CONTENT_MAX_BYTES;

/// 内存版 repo。`RwLock` 内部按 unique_id 串版本链。
#[derive(Debug, Default)]
pub struct MemoryGroupContextRepo {
    /// key = unique_id，value = 同链全部版本（含历史）。
    entries: RwLock<HashMap<String, Vec<ContextEntry>>>,
    templates: RwLock<Vec<PolicyTemplate>>,
    audits: RwLock<Vec<AuditEntry>>,
}

impl MemoryGroupContextRepo {
    pub fn new() -> Self {
        Self::default()
    }

    fn unique_id(granularity: Granularity, scope: &ScopeKey) -> String {
        scope.unique_id(granularity)
    }

    fn active_of<'a>(versions: &'a [ContextEntry]) -> Option<&'a ContextEntry> {
        // active = valid_to 为 None（首期用 tm 时 None 表 ∞）。
        versions.iter().rev().find(|e| e.time.valid_to.is_none())
    }
}

#[async_trait]
impl GroupContextRepoPort for MemoryGroupContextRepo {
    async fn find_active_in_scope(
        &self,
        granularity: Granularity,
        scope_key: &ScopeKey,
    ) -> ServiceResult<Option<String>> {
        let uid = Self::unique_id(granularity, scope_key);
        let map = self.entries.read().await;
        Ok(map
            .get(&uid)
            .and_then(|vs| Self::active_of(vs))
            .map(|e| e.context_id.clone()))
    }

    async fn insert_context(&self, request: InsertContextRequest) -> ServiceResult<()> {
        if request.entry.content.len() > CONTENT_MAX_BYTES {
            return Err(ServiceError::InvalidOperation {
                message: "content too large".to_string(),
                request_id: None,
            });
        }
        let mut map = self.entries.write().await;
        // 用 unique_id 聚合；context_id 作为层内主键查重。
        // （首期保实现最小可用：直接存进对应链尾）
        let uid = ScopeKey {
            tenant_id: request.entry.origin.tenant_id.clone(),
            group_id: request.entry.origin.group_id.clone(),
            session_id: request.entry.origin.session_id.clone(),
            run_id: request.entry.origin.run_id.clone(),
            domain: request.entry.consistency.domain.clone(),
        }
        .unique_id(request.entry.consistency.granularity);
        let chain = map.entry(uid).or_default();
        if Self::active_of(chain).is_some() {
            return Err(ServiceError::Conflict(
                "active version already exists".to_string(),
            ));
        }
        chain.push(request.entry);
        Ok(())
    }

    async fn supersede(&self, request: SupersedeRequest) -> ServiceResult<SupersedeOutcome> {
        let uid = request.scope_key.unique_id(request.granularity);
        let mut map = self.entries.write().await;
        let chain = map.get_mut(&uid).ok_or_else(|| ServiceError::InvalidOperation {
            message: format!("no active version to supersede for '{uid}'"),
            request_id: None,
        })?;
        // 取旧活跃版本（valid_to=None），回填，写新条目。顺序在写锁内，天然互斥。
        let old_idx = chain.iter().rposition(|e| e.time.valid_to.is_none());
        let Some(idx) = old_idx else {
            return Err(ServiceError::InvalidOperation {
                message: format!("no active version to supersede for '{uid}'"),
                request_id: None,
            });
        };
        let old_id = chain[idx].context_id.clone();
        chain[idx].time.valid_to = Some(request_now_millis());

        let now = request_now_millis();
        let new_entry = bcs_domain::ContextEntry {
            context_id: request.new_context_id.clone(),
            content: request.new_content,
            origin: request.new_origin.clone(),
            time: bcs_domain::ContextTime {
                valid_from: now,
                valid_to: None,
                tx_time: now,
            },
            flow: request.new_flow.clone(),
            consistency: request.new_consistency.clone(),
            lineage: bcs_domain::Lineage {
                supersedes: Some(old_id.clone()),
                superseded_reason: request.change_reason.clone(),
            },
            policy_version: "v0.1".to_string(), // 简化：不比对递增
            audit_ref: None,
        };
        chain.push(new_entry);
        Ok(SupersedeOutcome {
            new_context_id: request.new_context_id,
            superseded_id: old_id,
            policy_version: "v0.1".to_string(),
        })
    }

    async fn find_entries_by_domain(&self, domain: &str) -> ServiceResult<Vec<StoredEntry>> {
        let map = self.entries.read().await;
        let mut out = Vec::new();
        for vs in map.values() {
            for e in vs.iter() {
                if e.consistency.domain == domain {
                    out.push(StoredEntry {
                        entry: e.clone(),
                        policy: snapshot_of(e),
                    });
                }
            }
        }
        Ok(out)
    }

    async fn find_candidates_for_retrieve(
        &self,
        domain: &str,
        _scope: &bcs_domain::Origin,
    ) -> ServiceResult<Vec<StoredEntry>> {
        self.find_entries_by_domain(domain).await
    }

    async fn list_active_contexts_for_actor(
        &self,
        _scope: &bcs_domain::Origin,
    ) -> ServiceResult<Vec<ContextView>> {
        let map = self.entries.read().await;
        let mut out = Vec::new();
        for vs in map.values() {
            if let Some(e) = Self::active_of(vs) {
                out.push(ContextView {
                    domain: e.consistency.domain.clone(),
                    granularity: e.consistency.granularity,
                    description: String::new(),
                    permission: bcs_domain::Permission::R,
                });
            }
        }
        Ok(out)
    }

    async fn list_creatable_templates_for_actor(
        &self,
        _scope: &bcs_domain::Origin,
    ) -> ServiceResult<Vec<TemplateView>> {
        let tpls = self.templates.read().await;
        Ok(tpls
            .iter()
            .map(|t| TemplateView {
                template_id: t.template_id.clone(),
                granularity: t.consistency.granularity,
                description: t.description.clone(),
                params: t.params.iter().map(|p| p.name.clone()).collect(),
            })
            .collect())
    }

    async fn find_policy_template(
        &self,
        template_id: &str,
    ) -> ServiceResult<Option<PolicyTemplate>> {
        Ok(self
            .templates
            .read()
            .await
            .iter()
            .find(|t| t.template_id == template_id)
            .cloned())
    }

    async fn list_policy_templates(
        &self,
        _scope: &bcs_domain::Origin,
    ) -> ServiceResult<Vec<PolicyTemplate>> {
        Ok(self.templates.read().await.clone())
    }

    async fn write_retrieve_audit(&self, entry: AuditEntry) -> ServiceResult<()> {
        self.audits.write().await.push(entry);
        Ok(())
    }
}

fn request_now_millis() -> i64 {
    use chrono::Utc;
    Utc::now().timestamp_millis()
}

fn snapshot_of(e: &ContextEntry) -> bcs_domain::PolicySnapshot {
    use bcs_domain::PolicySnapshot;
    PolicySnapshot {
        context_id: e.context_id.clone(),
        version: e.policy_version.clone(),
        domain: e.consistency.domain.clone(),
        granularity: e.consistency.granularity,
        collect_from_json: serde_json::to_string(&e.flow.collect_from).unwrap_or_default(),
        visible_to_json: serde_json::to_string(&e.flow.visible_to).unwrap_or_default(),
        freshness_class: e.consistency.freshness_class,
        revalidate_due: e.consistency.revalidate_due,
        obligations: None,
    }
}

// 让 memory repo 能被 Arc<dyn GroupContextRepoPort> 装入 core。
pub type SharedMemoryGroupContextRepo = Arc<MemoryGroupContextRepo>;
