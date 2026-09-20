//! GroupContextManagement —— `GroupContextManagementService` 的实现（application 编排层）。
//!
//! 职责（CLAUDE.md）：HTTP DTO ↔ core Command 翻译；ServiceResult →
//! `GroupContextUseCaseError` 映射（供 delivery adapter 定 HTTP 状态码）。
//! 持 `Arc<dyn GroupContextCoreService>`，不直接碰 repo / DB。
//!
//! 给 Java 出身读者（非框线块，避免显示错乱）：
//!   这一层 ≈ Spring 的 Service，对 Controller 暴露应用级方法，把底层异常
//!   翻成可控的业务异常。ServiceError(底层) → UseCaseError(应用) 的映射在
//!   `map_err` / `map_core_error` 里，对照 plan.md §2.5 错误码表。

use std::sync::Arc;

use async_trait::async_trait;
use chrono::{TimeZone, Utc};

use bcs_domain::Origin;
use bcs_service_api::application::{
    CreateByTemplateRequest, CreateByTemplateResponse, GroupContextManagementService,
    GroupContextUseCaseError, RetrieveRequest, RetrieveResponse, RetrieveResponseItem,
    StatusRequest, StatusResponse, UpdateContentRequest, UpdateContentResponse,
    StatusContext as AppContext, StatusTemplate as AppTemplate,
};
use bcs_service_api::core::{
    CreateByTemplateCommand, GroupContextCoreService, RetrieveCommand, StatusCommand,
    UpdateContentCommand,
};
use bcs_service_api::types::ServiceError;

#[derive(Clone)]
pub struct GroupContextManagement {
    core: Arc<dyn GroupContextCoreService>,
}

impl GroupContextManagement {
    pub fn new(core: Arc<dyn GroupContextCoreService>) -> Self {
        Self { core }
    }
}

// ─── ServiceError → UseCaseError 映射（plan.md §2.5 错误码表）───────────────
//
// 把 core 抛的 ServiceError 翻成确定的用例错误：
//   Forbidden        → PermissionDenied  (403)
//   Conflict(String) → Conflict          (409)；若消息以 "ambiguous" 开头 → Ambiguous (409)
//   InvalidOperation → 视消息：
//     - 含 "exceeds limit"/"too large" → PayloadTooLarge (413)
//     - 含 "not found"/"no active"/"no writable" → NotFound (404)
//     - 否则 → InvalidParam (400)
//   其余              → Internal (500)
fn map_core_error(message: String) -> GroupContextUseCaseError {
    let msg = message.to_lowercase();
    if msg.contains("exceeds limit") || msg.contains("too large") {
        return GroupContextUseCaseError::PayloadTooLarge(message);
    }
    if msg.contains("not found") || msg.contains("no active") || msg.contains("no writable") {
        return GroupContextUseCaseError::NotFound(message);
    }
    if msg.starts_with("ambiguous") {
        return GroupContextUseCaseError::Ambiguous {
            message,
            candidates: Vec::new(),
        };
    }
    GroupContextUseCaseError::InvalidParam(message)
}

fn map_err(e: ServiceError) -> GroupContextUseCaseError {
    match e {
        ServiceError::Forbidden(m) => GroupContextUseCaseError::PermissionDenied(m),
        ServiceError::Conflict(m) => {
            if m.to_lowercase().starts_with("ambiguous") {
                GroupContextUseCaseError::Ambiguous {
                    message: m,
                    candidates: Vec::new(),
                }
            } else {
                GroupContextUseCaseError::Conflict(m)
            }
        }
        ServiceError::InvalidOperation { message, .. } => map_core_error(message),
        other => GroupContextUseCaseError::Internal(other),
    }
}

fn origin_from(req_tenant: &str, req_group: &Option<String>, req_session: &Option<String>,
    req_run: &Option<String>, req_actor: &str) -> Origin {
    Origin {
        tenant_id: req_tenant.to_string(),
        group_id: req_group.clone(),
        session_id: req_session.clone(),
        run_id: req_run.clone(),
        actor_id: req_actor.to_string(),
    }
}

#[async_trait]
impl GroupContextManagementService for GroupContextManagement {
    async fn status(&self, req: StatusRequest) -> Result<StatusResponse, GroupContextUseCaseError> {
        let origin = origin_from(
            &req.tenant_id, &req.group_id, &req.session_id, &req.run_id, &req.actor_id,
        );
        let result = self
            .core
            .status(StatusCommand { origin })
            .await
            .map_err(map_err)?;
        // core 的 ContextView/TemplateView → application 的 StatusContext/StatusTemplate。
        let contexts = result
            .contexts
            .into_iter()
            .map(|v| AppContext {
                domain: v.domain,
                granularity: v.granularity,
                description: v.description,
                permission: permission_str(v.permission),
            })
            .collect();
        let context_templates = result
            .context_templates
            .into_iter()
            .map(|t| AppTemplate {
                template_id: t.template_id,
                granularity: t.granularity,
                description: t.description,
                params: t.params,
            })
            .collect();
        Ok(StatusResponse {
            contexts,
            context_templates,
        })
    }

    async fn create_by_template(
        &self,
        req: CreateByTemplateRequest,
    ) -> Result<CreateByTemplateResponse, GroupContextUseCaseError> {
        let origin = origin_from(
            &req.tenant_id, &req.group_id, &req.session_id, &req.run_id, &req.actor_id,
        );
        let result = self
            .core
            .create_by_template(CreateByTemplateCommand {
                origin,
                template_id: req.template_id,
                content: req.content,
                params: req.params,
            })
            .await;
        match result {
            Ok(r) => Ok(CreateByTemplateResponse {
                status: "ok".to_string(),
                error_msg: None,
                context_id: Some(r.context_id),
                domain: Some(r.domain),
                granularity: Some(r.granularity),
                content: Some(r.content),
                superseded_id: r.superseded_id,
            }),
            Err(e) => {
                let (status, msg) = err_to_status(&map_err(e));
                Ok(CreateByTemplateResponse {
                    status,
                    error_msg: Some(msg),
                    context_id: None,
                    domain: None,
                    granularity: None,
                    content: None,
                    superseded_id: None,
                })
            }
        }
    }

    async fn update_content(
        &self,
        req: UpdateContentRequest,
    ) -> Result<UpdateContentResponse, GroupContextUseCaseError> {
        let origin = origin_from(
            &req.tenant_id, &req.group_id, &req.session_id, &req.run_id, &req.actor_id,
        );
        let result = self
            .core
            .update_content(UpdateContentCommand {
                origin,
                domain: req.domain,
                granularity: req.granularity,
                content: req.content,
                change_reason: req.change_reason,
            })
            .await;
        match result {
            Ok(r) => Ok(UpdateContentResponse {
                status: "ok".to_string(),
                error_msg: None,
                context_id: Some(r.context_id),
                domain: Some(r.domain),
                granularity: Some(r.granularity),
                content: Some(r.content),
                superseded_id: Some(r.superseded_id),
                change_reason: r.change_reason,
            }),
            Err(e) => {
                let (status, msg) = err_to_status(&map_err(e));
                Ok(UpdateContentResponse {
                    status,
                    error_msg: Some(msg),
                    context_id: None,
                    domain: None,
                    granularity: None,
                    content: None,
                    superseded_id: None,
                    change_reason: None,
                })
            }
        }
    }

    async fn retrieve(
        &self,
        req: RetrieveRequest,
    ) -> Result<RetrieveResponse, GroupContextUseCaseError> {
        let origin = origin_from(
            &req.tenant_id, &req.group_id, &req.session_id, &req.run_id, &req.actor_id,
        );
        let result = self
            .core
            .retrieve(RetrieveCommand {
                origin,
                domain: req.domain.clone(),
                limit: req.limit,
            })
            .await
            .map_err(map_err)?;
        let items = result
            .items
            .into_iter()
            .map(|i| RetrieveResponseItem {
                context_id: i.context_id,
                domain: i.domain,
                granularity: i.granularity,
                content: i.content,
                valid_from: Utc
                    .timestamp_millis_opt(i.valid_from)
                    .single()
                    .map(|t| t.to_rfc3339())
                    .unwrap_or_default(),
            })
            .collect();
        Ok(RetrieveResponse { items })
    }
}

fn permission_str(p: bcs_domain::Permission) -> String {
    match p {
        bcs_domain::Permission::W => "W",
        bcs_domain::Permission::R => "R",
        bcs_domain::Permission::WR => "WR",
    }
    .to_string()
}

/// 用例错误 → (status 字符串, 人类消息)，对齐 plan.md §2.5。
fn err_to_status(e: &GroupContextUseCaseError) -> (String, String) {
    match e {
        GroupContextUseCaseError::InvalidParam(m) => ("invalid_param".into(), m.clone()),
        GroupContextUseCaseError::PermissionDenied(m) => ("permission_denied".into(), m.clone()),
        GroupContextUseCaseError::NotFound(m) => ("not_found".into(), m.clone()),
        GroupContextUseCaseError::Conflict(m) => ("conflict".into(), m.clone()),
        GroupContextUseCaseError::Ambiguous { message, candidates } => {
            ("ambiguous".into(), if candidates.is_empty() {
                message.clone()
            } else {
                format!("{message}; candidates: {}", candidates.join(", "))
            })
        }
        GroupContextUseCaseError::PayloadTooLarge(m) => ("payload_too_large".into(), m.clone()),
        GroupContextUseCaseError::Internal(_) => ("internal_error".into(), "internal error".into()),
    }
}
