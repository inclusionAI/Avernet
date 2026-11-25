use super::*;
use bcs_service_api::application::agent_invoke::*;
use dto::Params;
pub(super) async fn exact(
    s: &AgentInvokeHttpServices,
    principal: AgentInvokePrincipal,
    path: &BTreeMap<String, String>,
    p: &Params,
) -> AgentInvokeResult<AgentInvokeQueryResult> {
    let direction = dto::direction(p)?;
    let filter = if let Some(id) = path.get("edge_id") {
        AgentInvokeQueryFilter::Grant {
            direction,
            edge_id: dto::positive(id)?,
        }
    } else {
        AgentInvokeQueryFilter::Request {
            direction,
            request_id: path
                .get("request_id")
                .cloned()
                .ok_or(AgentInvokeError::InvalidInput)?,
        }
    };
    s.queries
        .read(AgentInvokeQuery {
            context: AgentInvokeQueryContext {
                scope: s.scope.clone(),
                principal,
                target: dto::target(p)?,
                filter,
            },
            page: None,
        })
        .await
}
pub(super) async fn handle(
    s: &AgentInvokeHttpServices,
    principal: AgentInvokePrincipal,
    route: &str,
    path: &BTreeMap<String, String>,
    p: Params,
    id: &RequestId,
) -> AgentInvokeResult<Response> {
    let value = if route.ends_with("/{edge_id}") || route.ends_with("/{request_id}") {
        dto::allowed(&p, &["bot_id", "peer_bot_id", "direction"])?;
        exact(s, principal, path, &p).await?
    } else {
        let suffix = route
            .rsplit('/')
            .next()
            .ok_or(AgentInvokeError::InvalidInput)?;
        let filter = match suffix {
            "grants" => {
                dto::allowed(
                    &p,
                    &[
                        "bot_id",
                        "peer_bot_id",
                        "direction",
                        "status",
                        "limit",
                        "cursor",
                    ],
                )?;
                let status = match p.get("status").map(String::as_str).unwrap_or("approved") {
                    "approved" => EdgeStatus::Approved,
                    "revoked" => EdgeStatus::Revoked,
                    _ => return Err(AgentInvokeError::InvalidInput),
                };
                AgentInvokeQueryFilter::Grants {
                    direction: dto::direction(&p)?,
                    status,
                }
            }
            "requests" => {
                dto::allowed(
                    &p,
                    &[
                        "bot_id",
                        "peer_bot_id",
                        "direction",
                        "status",
                        "limit",
                        "cursor",
                    ],
                )?;
                let status = match p.get("status").map(String::as_str).unwrap_or("pending") {
                    "pending" => RequestStatus::Pending,
                    "approved" => RequestStatus::Approved,
                    "rejected" => RequestStatus::Rejected,
                    "cancelled" => RequestStatus::Cancelled,
                    _ => return Err(AgentInvokeError::InvalidInput),
                };
                AgentInvokeQueryFilter::Requests {
                    direction: dto::direction(&p)?,
                    status,
                }
            }
            "degrees" => {
                dto::allowed(&p, &["bot_id", "peer_bot_id"])?;
                AgentInvokeQueryFilter::Degrees
            }
            "audit" => {
                dto::allowed(
                    &p,
                    &[
                        "bot_id",
                        "peer_bot_id",
                        "direction",
                        "action",
                        "limit",
                        "cursor",
                    ],
                )?;
                let action = p
                    .get("action")
                    .map(|name| {
                        [
                            AgentInvokeAuditAction::Request,
                            AgentInvokeAuditAction::Grant,
                            AgentInvokeAuditAction::Approve,
                            AgentInvokeAuditAction::Reject,
                            AgentInvokeAuditAction::Cancel,
                            AgentInvokeAuditAction::Revoke,
                            AgentInvokeAuditAction::QualificationPublish,
                            AgentInvokeAuditAction::QualificationInvalidate,
                            AgentInvokeAuditAction::DelegationPublish,
                        ]
                        .into_iter()
                        .find(|a| a.as_str() == name)
                        .ok_or(AgentInvokeError::InvalidInput)
                    })
                    .transpose()?;
                AgentInvokeQueryFilter::Audit {
                    direction: dto::direction(&p)?,
                    action,
                }
            }
            _ => return Err(AgentInvokeError::InvalidInput),
        };
        let context = AgentInvokeQueryContext {
            scope: s.scope.clone(),
            principal,
            target: dto::target(&p)?,
            filter,
        };
        let page = if context.filter.is_page() {
            Some(dto::page(&context, &p)?)
        } else {
            None
        };
        s.queries.read(AgentInvokeQuery { context, page }).await?
    };
    Ok(success(id, StatusCode::OK, dto::result(value)?))
}
