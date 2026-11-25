use super::*;
use bcs_domain::{
    agent_invoke::validate_agent_invoke_shape,
    edge_permission::{GrantKind, RequestStatus},
};
fn optional(row: &DbRow, key: &str) -> AgentInvokeResult<Option<String>> {
    match row.get(key) {
        Some(DbValue::Null) => Ok(None),
        Some(_) => string(row, key).map(Some),
        None => Err(AgentInvokeError::InvalidState),
    }
}
fn peers(
    row: &DbRow,
    c: &AgentInvokeQueryContext,
    d: AgentInvokeDirection,
) -> AgentInvokeResult<(AgentInvokePeerSummary, AgentInvokePeerSummary)> {
    let from = string(row, "from_id")?;
    let to = string(row, "to_id")?;
    DbAgentInvokeStore::validate_pair(&AgentInvokePair {
        from_bot_id: from.clone(),
        to_bot_id: to.clone(),
    })
    .map_err(|_| AgentInvokeError::InvalidState)?;
    let (anchor, peer) = match d {
        AgentInvokeDirection::Outbound => (&from, &to),
        AgentInvokeDirection::Inbound => (&to, &from),
    };
    if anchor != &c.target.bot_id || c.target.peer_bot_id.as_ref().is_some_and(|id| id != peer) {
        return Err(AgentInvokeError::InvalidState);
    }
    Ok((
        AgentInvokePeerSummary {
            bot_id: from,
            name: optional(row, "from_name")?,
            agent_code: optional(row, "from_code")?,
        },
        AgentInvokePeerSummary {
            bot_id: to,
            name: optional(row, "to_name")?,
            agent_code: optional(row, "to_code")?,
        },
    ))
}
fn null(row: &DbRow, key: &str) -> AgentInvokeResult<()> {
    if row.get(key) != Some(&DbValue::Null) {
        Err(AgentInvokeError::InvalidState)
    } else {
        Ok(())
    }
}
fn grant(
    row: &DbRow,
    c: &AgentInvokeQueryContext,
    d: AgentInvokeDirection,
    scope: u64,
) -> AgentInvokeResult<AgentInvokeGrantRecord> {
    if string(row, "env")? != c.scope.env || positive(row, "metadata_scope_id")?.get() != scope {
        return Err(AgentInvokeError::InvalidState);
    }
    let edge =
        crate::mapping::row_to_edge_grant(row).map_err(|_| AgentInvokeError::InvalidState)?;
    if edge.grant_kind != GrantKind::AgentInvoke || validate_agent_invoke_shape(&edge).is_err() {
        return Err(AgentInvokeError::InvalidState);
    }
    null(row, "rules")?;
    null(row, "originator_policy_data")?;
    let (from, to) = peers(row, c, d)?;
    Ok(AgentInvokeGrantRecord {
        grant: AgentInvokeGrantView {
            edge_id: positive(row, "id")?,
            status: edge.status,
            version: positive(row, "metadata_version")?,
        },
        from,
        to,
        created_at_ms: number(row, "created_at_ms")?,
        updated_at_ms: number(row, "updated_at_ms")?,
    })
}
fn request(
    row: &DbRow,
    c: &AgentInvokeQueryContext,
    d: AgentInvokeDirection,
    scope: u64,
) -> AgentInvokeResult<AgentInvokeRequestRecord> {
    if string(row, "env")? != c.scope.env
        || string(row, "request_kind")? != "agent_invoke"
        || positive(row, "metadata_scope_id")?.get() != scope
        || number(row, "requested_ref_id")? != 0
        || !matches!(
            string(row, "creator_kind")?.as_str(),
            "human" | "governance"
        )
    {
        return Err(AgentInvokeError::InvalidState);
    }
    null(row, "requested_rules")?;
    let status = match string(row, "status")?.as_str() {
        "pending" => RequestStatus::Pending,
        "approved" => RequestStatus::Approved,
        "rejected" => RequestStatus::Rejected,
        "cancelled" => RequestStatus::Cancelled,
        _ => return Err(AgentInvokeError::InvalidState),
    };
    let edge_id = if row.get("edge_id") == Some(&DbValue::Null) {
        None
    } else {
        Some(positive(row, "edge_id")?)
    };
    if (status == RequestStatus::Approved) != edge_id.is_some() {
        return Err(AgentInvokeError::InvalidState);
    }
    let request_id = string(row, "request_id")?;
    if uuid::Uuid::parse_str(&request_id).is_err() {
        return Err(AgentInvokeError::InvalidState);
    }
    let (from, to) = peers(row, c, d)?;
    Ok(AgentInvokeRequestRecord {
        request: AgentInvokeRequestView {
            request_id,
            status,
            version: positive(row, "metadata_version")?,
            edge_id,
        },
        from,
        to,
        created_at_ms: crate::mapping::required_timestamp_epoch_ms(row, "gmt_create")
            .map_err(|_| AgentInvokeError::InvalidState)?,
        updated_at_ms: crate::mapping::required_timestamp_epoch_ms(row, "gmt_modified")
            .map_err(|_| AgentInvokeError::InvalidState)?,
    })
}
fn audit(
    row: &DbRow,
    c: &AgentInvokeQueryContext,
    d: AgentInvokeDirection,
) -> AgentInvokeResult<AgentInvokeAuditRecord> {
    let expected = pair(c, d).ok_or(AgentInvokeError::InvalidState)?;
    if string(row, "from_id")? != expected.from_bot_id
        || string(row, "to_id")? != expected.to_bot_id
    {
        return Err(AgentInvokeError::InvalidState);
    }
    let action = string(row, "action")?;
    let action = [
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
    .find(|a| a.as_str() == action)
    .ok_or(AgentInvokeError::InvalidState)?;
    if let AgentInvokeQueryFilter::Audit {
        action: Some(filter),
        ..
    } = c.filter
    {
        if action != filter {
            return Err(AgentInvokeError::InvalidState);
        }
    }
    let principal_kind = string(row, "principal_kind")?;
    if !matches!(
        principal_kind.as_str(),
        "human" | "governance" | "inventory"
    ) {
        return Err(AgentInvokeError::InvalidState);
    }
    let action_id = string(row, "action_id")?;
    let principal_id = string(row, "principal_id")?;
    let reason = string(row, "reason")?;
    if action_id.is_empty()
        || principal_id.trim().is_empty()
        || reason.trim().is_empty()
        || reason.len() > 2048
    {
        return Err(AgentInvokeError::InvalidState);
    }
    Ok(AgentInvokeAuditRecord {
        id: positive(row, "id")?,
        action_id,
        action,
        pair: expected,
        principal_kind,
        principal_id,
        reason,
        created_at_ms: number(row, "created_at_ms")?,
    })
}
fn page<T>(
    q: &AgentInvokeQuery,
    mut items: Vec<T>,
    positions: Vec<AgentInvokeCursorPosition>,
) -> AgentInvokeResult<AgentInvokeQueryRows<T>> {
    let limit = q
        .page
        .as_ref()
        .ok_or(AgentInvokeError::InvalidState)?
        .limit
        .get() as usize;
    if items.len() > limit + 1 {
        return Err(AgentInvokeError::InvalidState);
    }
    let next_cursor = if items.len() > limit {
        let position = positions
            .get(limit - 1)
            .ok_or(AgentInvokeError::InvalidState)?
            .clone();
        items.truncate(limit);
        Some(AgentInvokeQueryCursor {
            context: q.context.clone(),
            position,
        })
    } else {
        None
    };
    Ok(AgentInvokeQueryRows { items, next_cursor })
}
pub(super) fn result(
    _s: &DbAgentInvokeStore,
    q: &AgentInvokeQuery,
    scope: u64,
    results: &[DbTransactionStepResult],
) -> AgentInvokeResult<AgentInvokeQueryData> {
    if matches!(q.context.filter, AgentInvokeQueryFilter::Degrees) {
        if results.len() != 4 {
            return Err(AgentInvokeError::InvalidState);
        }
        let mut counts = Vec::new();
        for i in 0..4 {
            let [row] = rows(results, i)? else {
                return Err(AgentInvokeError::InvalidState);
            };
            if number(row, "bad")? != 0 {
                return Err(AgentInvokeError::InvalidState);
            }
            counts.push(number(row, "n")?);
        }
        return Ok(AgentInvokeQueryData::Degrees(AgentInvokeDegrees {
            out_degree: counts[0],
            in_degree: counts[1],
            pending_out: counts[2],
            pending_in: counts[3],
            count_basis: AgentInvokeCountBasis::StoredApproved,
        }));
    }
    if results.len() != 1 {
        return Err(AgentInvokeError::InvalidState);
    }
    let data = rows(results, 0)?;
    let direction = q
        .context
        .filter
        .direction()
        .ok_or(AgentInvokeError::InvalidState)?;
    if let AgentInvokeQueryFilter::Grant { edge_id, .. } = &q.context.filter {
        let [row] = data else {
            return Err(if data.is_empty() {
                AgentInvokeError::NotFound
            } else {
                AgentInvokeError::InvalidState
            });
        };
        let value = grant(row, &q.context, direction, scope)?;
        if value.grant.edge_id != *edge_id {
            return Err(AgentInvokeError::InvalidState);
        }
        return Ok(AgentInvokeQueryData::Grant(value));
    }
    if let AgentInvokeQueryFilter::Request { request_id, .. } = &q.context.filter {
        let [row] = data else {
            return Err(if data.is_empty() {
                AgentInvokeError::NotFound
            } else {
                AgentInvokeError::InvalidState
            });
        };
        let value = request(row, &q.context, direction, scope)?;
        if value.request.request_id != *request_id {
            return Err(AgentInvokeError::InvalidState);
        }
        return Ok(AgentInvokeQueryData::Request(value));
    }
    let mut positions = Vec::new();
    let mut previous = q
        .page
        .as_ref()
        .and_then(|p| p.cursor.as_ref())
        .map(|c| c.position.clone());
    for row in data {
        let id = positive(row, "id")?;
        let pos = if matches!(q.context.filter, AgentInvokeQueryFilter::Audit { .. }) {
            AgentInvokeCursorPosition::AuditTime {
                created_at_ms: number(row, "created_at_ms")?,
                id,
            }
        } else {
            AgentInvokeCursorPosition::RowId(id)
        };
        if let Some(old) = &previous {
            let later = match (old, &pos) {
                (AgentInvokeCursorPosition::RowId(a), AgentInvokeCursorPosition::RowId(b)) => b > a,
                (
                    AgentInvokeCursorPosition::AuditTime {
                        created_at_ms: a,
                        id: ai,
                    },
                    AgentInvokeCursorPosition::AuditTime {
                        created_at_ms: b,
                        id: bi,
                    },
                ) => (b, bi) > (a, ai),
                _ => false,
            };
            if !later {
                return Err(AgentInvokeError::InvalidState);
            }
        }
        previous = Some(pos.clone());
        positions.push(pos);
    }
    match &q.context.filter {
        AgentInvokeQueryFilter::Grants { status, .. } => {
            let items = data
                .iter()
                .map(|r| {
                    let v = grant(r, &q.context, direction, scope)?;
                    if v.grant.status != *status {
                        return Err(AgentInvokeError::InvalidState);
                    }
                    Ok(v)
                })
                .collect::<AgentInvokeResult<Vec<_>>>()?;
            Ok(AgentInvokeQueryData::Grants(page(q, items, positions)?))
        }
        AgentInvokeQueryFilter::Requests { status, .. } => {
            let items = data
                .iter()
                .map(|r| {
                    let v = request(r, &q.context, direction, scope)?;
                    if v.request.status != *status {
                        return Err(AgentInvokeError::InvalidState);
                    }
                    Ok(v)
                })
                .collect::<AgentInvokeResult<Vec<_>>>()?;
            Ok(AgentInvokeQueryData::Requests(page(q, items, positions)?))
        }
        AgentInvokeQueryFilter::Audit { .. } => {
            let items = data
                .iter()
                .map(|r| audit(r, &q.context, direction))
                .collect::<AgentInvokeResult<Vec<_>>>()?;
            Ok(AgentInvokeQueryData::Audit(page(q, items, positions)?))
        }
        _ => Err(AgentInvokeError::InvalidState),
    }
}
