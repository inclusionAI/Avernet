//! HTTP-only, strict wire DTOs. Trusted application context is never deserialized.
use bcs_service_api::application::agent_invoke::*;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    num::{NonZeroU16, NonZeroU64},
};
pub type Params = BTreeMap<String, String>;
pub fn params(query: Option<&str>) -> AgentInvokeResult<Params> {
    let query = query.unwrap_or("");
    if query.len() > 8192 {
        return Err(AgentInvokeError::InvalidInput);
    }
    let mut p = Params::new();
    for (key, value) in url::form_urlencoded::parse(query.as_bytes()) {
        if p.insert(key.into_owned(), value.into_owned()).is_some() {
            return Err(AgentInvokeError::InvalidInput);
        }
    }
    Ok(p)
}
pub fn allowed(p: &Params, names: &[&str]) -> AgentInvokeResult<()> {
    if p.keys().any(|k| !names.contains(&k.as_str())) {
        return Err(AgentInvokeError::InvalidInput);
    }
    Ok(())
}
pub fn positive(s: &str) -> AgentInvokeResult<NonZeroU64> {
    s.parse::<u64>()
        .ok()
        .filter(|n| *n <= i64::MAX as u64)
        .and_then(NonZeroU64::new)
        .ok_or(AgentInvokeError::InvalidInput)
}
pub fn target(p: &Params) -> AgentInvokeResult<AgentInvokeQueryTarget> {
    Ok(AgentInvokeQueryTarget {
        bot_id: p
            .get("bot_id")
            .cloned()
            .ok_or(AgentInvokeError::InvalidInput)?,
        peer_bot_id: p.get("peer_bot_id").cloned(),
    })
}
pub fn direction(p: &Params) -> AgentInvokeResult<AgentInvokeDirection> {
    match p.get("direction").map(String::as_str) {
        Some("inbound") => Ok(AgentInvokeDirection::Inbound),
        Some("outbound") => Ok(AgentInvokeDirection::Outbound),
        _ => Err(AgentInvokeError::InvalidInput),
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrantBody {
    pub from_bot_id: String,
    pub to_bot_id: String,
    pub expected_version: u64,
    pub reason: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RequestBody {
    pub from_bot_id: String,
    pub to_bot_id: String,
    pub reason: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VersionBody {
    pub expected_version: NonZeroU64,
    pub reason: String,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cursor {
    context: String,
    id: NonZeroU64,
    time: Option<u64>,
}
fn fingerprint(c: &AgentInvokeQueryContext) -> String {
    let direction = |d: AgentInvokeDirection| match d {
        AgentInvokeDirection::Inbound => "inbound",
        AgentInvokeDirection::Outbound => "outbound",
    };
    let filter = match &c.filter {
        AgentInvokeQueryFilter::Grant {
            direction: d,
            edge_id,
        } => json!(["grant", direction(*d), edge_id]),
        AgentInvokeQueryFilter::Request {
            direction: d,
            request_id,
        } => json!(["request", direction(*d), request_id]),
        AgentInvokeQueryFilter::Grants {
            direction: d,
            status,
        } => json!([
            "grants",
            direction(*d),
            match status {
                EdgeStatus::Approved => "approved",
                EdgeStatus::Revoked => "revoked",
            }
        ]),
        AgentInvokeQueryFilter::Requests {
            direction: d,
            status,
        } => json!([
            "requests",
            direction(*d),
            match status {
                RequestStatus::Pending => "pending",
                RequestStatus::Approved => "approved",
                RequestStatus::Rejected => "rejected",
                RequestStatus::Cancelled => "cancelled",
            }
        ]),
        AgentInvokeQueryFilter::Degrees => json!(["degrees"]),
        AgentInvokeQueryFilter::Audit {
            direction: d,
            action,
        } => json!([
            "audit",
            direction(*d),
            action.map(AgentInvokeAuditAction::as_str)
        ]),
    };
    let kind = match c.principal {
        AgentInvokePrincipal::Human(_) => "human",
        AgentInvokePrincipal::Agent(_) => "agent",
        AgentInvokePrincipal::Governance(_) => "governance",
    };
    let canonical = json!([
        "agent-invoke-cursor-v1",
        c.scope.namespace,
        c.scope.env,
        kind,
        c.principal.id(),
        c.target.bot_id,
        c.target.peer_bot_id,
        filter
    ]);
    format!("{:x}", Sha256::digest(canonical.to_string().as_bytes()))
}
pub fn page(c: &AgentInvokeQueryContext, p: &Params) -> AgentInvokeResult<AgentInvokeQueryPage> {
    let limit = p
        .get("limit")
        .map(|n| n.parse::<u16>().map_err(|_| AgentInvokeError::InvalidInput))
        .transpose()?
        .unwrap_or(50);
    let limit = NonZeroU16::new(limit)
        .filter(|n| n.get() <= 100)
        .ok_or(AgentInvokeError::InvalidInput)?;
    let cursor = p
        .get("cursor")
        .map(|raw| {
            if raw.len() > 2048 || !raw.starts_with("v1.") {
                return Err(AgentInvokeError::InvalidInput);
            }
            let raw = urlencoding::decode(&raw[3..]).map_err(|_| AgentInvokeError::InvalidInput)?;
            let wire: Cursor =
                serde_json::from_str(&raw).map_err(|_| AgentInvokeError::InvalidInput)?;
            if wire.context != fingerprint(c) || wire.id.get() > i64::MAX as u64 {
                return Err(AgentInvokeError::InvalidInput);
            }
            let position = match (&c.filter, wire.time) {
                (AgentInvokeQueryFilter::Audit { .. }, Some(created_at_ms))
                    if created_at_ms <= i64::MAX as u64 =>
                {
                    AgentInvokeCursorPosition::AuditTime {
                        created_at_ms,
                        id: wire.id,
                    }
                }
                (
                    AgentInvokeQueryFilter::Grants { .. } | AgentInvokeQueryFilter::Requests { .. },
                    None,
                ) => AgentInvokeCursorPosition::RowId(wire.id),
                _ => return Err(AgentInvokeError::InvalidInput),
            };
            Ok(AgentInvokeQueryCursor {
                context: c.clone(),
                position,
            })
        })
        .transpose()?;
    Ok(AgentInvokeQueryPage { limit, cursor })
}
fn cursor(c: Option<AgentInvokeQueryCursor>) -> AgentInvokeResult<Option<String>> {
    c.map(|c| {
        let (id, time) = match c.position {
            AgentInvokeCursorPosition::RowId(id) => (id, None),
            AgentInvokeCursorPosition::AuditTime { created_at_ms, id } => (id, Some(created_at_ms)),
        };
        let raw = serde_json::to_string(&Cursor {
            context: fingerprint(&c.context),
            id,
            time,
        })
        .map_err(|_| AgentInvokeError::InvalidState)?;
        Ok(format!("v1.{}", urlencoding::encode(&raw)))
    })
    .transpose()
}
pub fn grant_view(g: &AgentInvokeGrantView) -> Value {
    json!({"edge_id":g.edge_id,"status":match g.status {EdgeStatus::Approved=>"approved",EdgeStatus::Revoked=>"revoked"},"version":g.version})
}
pub fn request_view(r: &AgentInvokeRequestView) -> Value {
    json!({"request_id":r.request_id,"edge_id":r.edge_id,"version":r.version,"status":match r.status {RequestStatus::Pending=>"pending",RequestStatus::Approved=>"approved",RequestStatus::Rejected=>"rejected",RequestStatus::Cancelled=>"cancelled"}})
}
fn grant(g: AgentInvokeGrantRecord) -> Value {
    let mut v = grant_view(&g.grant);
    v["grant_kind"] = json!("agent_invoke");
    for (key, value) in [
        ("from_bot_id", json!(g.from.bot_id)),
        ("to_bot_id", json!(g.to.bot_id)),
        ("from_name", json!(g.from.name)),
        ("to_name", json!(g.to.name)),
        ("from_agent_code", json!(g.from.agent_code)),
        ("to_agent_code", json!(g.to.agent_code)),
        ("created_at_ms", json!(g.created_at_ms)),
        ("updated_at_ms", json!(g.updated_at_ms)),
    ] {
        v[key] = value;
    }
    v
}
fn request(r: AgentInvokeRequestRecord) -> Value {
    let mut v = request_view(&r.request);
    v["request_kind"] = json!("agent_invoke");
    for (key, value) in [
        ("from_bot_id", json!(r.from.bot_id)),
        ("to_bot_id", json!(r.to.bot_id)),
        ("from_name", json!(r.from.name)),
        ("to_name", json!(r.to.name)),
        ("from_agent_code", json!(r.from.agent_code)),
        ("to_agent_code", json!(r.to.agent_code)),
        ("created_at_ms", json!(r.created_at_ms)),
        ("updated_at_ms", json!(r.updated_at_ms)),
    ] {
        v[key] = value;
    }
    v
}
pub fn result(result: AgentInvokeQueryResult) -> AgentInvokeResult<Value> {
    let data = match result.data {
        AgentInvokeQueryData::Grant(g) => grant(g),
        AgentInvokeQueryData::Request(r) => request(r),
        AgentInvokeQueryData::Grants(p) => {
            json!({"items":p.items.into_iter().map(grant).collect::<Vec<_>>(),"next_cursor":cursor(p.next_cursor)?})
        }
        AgentInvokeQueryData::Requests(p) => {
            json!({"items":p.items.into_iter().map(request).collect::<Vec<_>>(),"next_cursor":cursor(p.next_cursor)?})
        }
        AgentInvokeQueryData::Degrees(d) => {
            json!({"out_degree":d.out_degree,"in_degree":d.in_degree,"pending_out":d.pending_out,"pending_in":d.pending_in,"count_basis":"stored_approved"})
        }
        AgentInvokeQueryData::Audit(p) => {
            json!({"items":p.items.into_iter().map(|r|json!({"id":r.id,"action_id":r.action_id,"action":r.action.as_str(),"from_bot_id":r.pair.from_bot_id,"to_bot_id":r.pair.to_bot_id,"principal_kind":r.principal_kind,"principal_id":r.principal_id,"reason":r.reason,"created_at_ms":r.created_at_ms})).collect::<Vec<_>>(),"next_cursor":cursor(p.next_cursor)?})
        }
    };
    Ok(json!({"snapshot_at_ms":result.snapshot_at_ms,"result":data}))
}
