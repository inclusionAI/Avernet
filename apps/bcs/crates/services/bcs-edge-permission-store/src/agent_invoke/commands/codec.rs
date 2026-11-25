use super::*;
use serde_json::{Value, json};

pub(super) fn principal_kind(p: &AgentInvokePrincipal) -> &'static str {
    match p {
        AgentInvokePrincipal::Human(_) => "human",
        AgentInvokePrincipal::Agent(_) => "agent",
        AgentInvokePrincipal::Governance(_) => "governance",
    }
}
pub(super) fn action_name(a: AgentInvokeAction) -> &'static str {
    match a {
        AgentInvokeAction::Request => "request",
        AgentInvokeAction::Grant => "grant",
        AgentInvokeAction::Approve => "approve",
        AgentInvokeAction::Reject => "reject",
        AgentInvokeAction::Cancel => "cancel",
        AgentInvokeAction::Revoke => "revoke",
        AgentInvokeAction::Query => "query",
    }
}
pub(super) fn command_json(c: &AgentInvokeCommand) -> String {
    let detail = match &c.change {
        AgentInvokeChange::Grant { expected_version } => {
            json!({"expected_version":expected_version})
        }
        AgentInvokeChange::Request => json!({}),
        AgentInvokeChange::Approve {
            request_id,
            expected_version,
        }
        | AgentInvokeChange::Reject {
            request_id,
            expected_version,
        }
        | AgentInvokeChange::Cancel {
            request_id,
            expected_version,
        } => json!({"request_id":request_id,"expected_version":expected_version.get()}),
        AgentInvokeChange::Revoke {
            edge_id,
            expected_version,
        } => json!({"edge_id":edge_id.get(),"expected_version":expected_version.get()}),
    };
    json!({"principal":[principal_kind(&c.principal),c.principal.id()],"scope":[c.authenticated_scope.namespace,c.authenticated_scope.env],"pair":[c.pair.from_bot_id,c.pair.to_bot_id],"action":action_name(c.change.action()),"detail":detail,"reason":c.reason,"key":c.idempotency_key}).to_string()
}
pub(super) fn grant_view(
    approval: &AgentInvokeApproval,
) -> AgentInvokeResult<AgentInvokeGrantView> {
    Ok(AgentInvokeGrantView {
        edge_id: NonZeroU64::new(approval.edge.edge_id).ok_or(AgentInvokeError::InvalidState)?,
        status: approval.edge.status,
        version: approval.version,
    })
}
pub(super) fn request_view(row: &DbRow) -> AgentInvokeResult<AgentInvokeRequestView> {
    Ok(AgentInvokeRequestView {
        request_id: string(row, "request_id")?,
        status: match string(row, "status")?.as_str() {
            "pending" => RequestStatus::Pending,
            "approved" => RequestStatus::Approved,
            "rejected" => RequestStatus::Rejected,
            "cancelled" => RequestStatus::Cancelled,
            _ => return Err(AgentInvokeError::InvalidState),
        },
        version: positive(row, "request_version")?,
        edge_id: match row.get("edge_id") {
            Some(DbValue::Null) => None,
            _ => Some(positive(row, "edge_id")?),
        },
    })
}
fn num(v: &Value, key: &str) -> AgentInvokeResult<u64> {
    v.get(key)
        .and_then(Value::as_u64)
        .filter(|n| *n <= i64::MAX as u64)
        .ok_or(AgentInvokeError::InvalidState)
}
fn pos(v: &Value, key: &str) -> AgentInvokeResult<NonZeroU64> {
    NonZeroU64::new(num(v, key)?).ok_or(AgentInvokeError::InvalidState)
}
fn text(v: &Value, key: &str) -> AgentInvokeResult<String> {
    v.get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or(AgentInvokeError::InvalidState)
}
pub(super) fn decode_receipt(s: &str) -> AgentInvokeResult<AgentInvokeReceipt> {
    let v: Value = serde_json::from_str(s).map_err(|_| AgentInvokeError::InvalidState)?;
    let grant = |key| -> AgentInvokeResult<Option<AgentInvokeGrantView>> {
        let x = v.get(key).ok_or(AgentInvokeError::InvalidState)?;
        if x.is_null() {
            return Ok(None);
        }
        Ok(Some(AgentInvokeGrantView {
            edge_id: pos(x, "edge_id")?,
            version: pos(x, "version")?,
            status: match text(x, "status")?.as_str() {
                "approved" => EdgeStatus::Approved,
                "revoked" => EdgeStatus::Revoked,
                _ => return Err(AgentInvokeError::InvalidState),
            },
        }))
    };
    let request = |key| -> AgentInvokeResult<Option<AgentInvokeRequestView>> {
        let x = v.get(key).ok_or(AgentInvokeError::InvalidState)?;
        if x.is_null() {
            return Ok(None);
        }
        Ok(Some(AgentInvokeRequestView {
            request_id: text(x, "request_id")?,
            version: pos(x, "version")?,
            status: match text(x, "status")?.as_str() {
                "pending" => RequestStatus::Pending,
                "approved" => RequestStatus::Approved,
                "rejected" => RequestStatus::Rejected,
                "cancelled" => RequestStatus::Cancelled,
                _ => return Err(AgentInvokeError::InvalidState),
            },
            edge_id: if x.get("edge_id") == Some(&Value::Null) {
                None
            } else {
                Some(pos(x, "edge_id")?)
            },
        }))
    };
    Ok(AgentInvokeReceipt {
        action_id: text(&v, "action_id")?,
        replayed: false,
        original_grant: grant("original_grant")?,
        original_request: request("original_request")?,
        current_grant: grant("current_grant")?,
        current_request: request("current_request")?,
    })
}
pub(super) fn view_json(
    grant: Option<&AgentInvokeGrantView>,
    request: Option<&AgentInvokeRequestView>,
) -> String {
    json!({"grant":grant.map(|g|json!({"edge_id":g.edge_id.get(),"version":g.version.get(),"status":if g.status==EdgeStatus::Approved {"approved"}else{"revoked"}})),"request":request.map(|r|json!({"request_id":r.request_id,"version":r.version.get(),"status":request_status(r.status),"edge_id":r.edge_id.map(NonZeroU64::get)}))}).to_string()
}
pub(super) fn request_status(s: RequestStatus) -> &'static str {
    match s {
        RequestStatus::Pending => "pending",
        RequestStatus::Approved => "approved",
        RequestStatus::Rejected => "rejected",
        RequestStatus::Cancelled => "cancelled",
    }
}
