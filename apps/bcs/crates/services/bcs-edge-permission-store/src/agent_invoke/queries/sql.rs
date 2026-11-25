//! Fixed directed index ranges. No bidirectional OR, OFFSET or per-edge calls.
use super::*;
use bcs_db_api::DbTransactionParam as P;
use bcs_domain::edge_permission::{EdgeStatus, RequestStatus};
fn edge_status(s: EdgeStatus) -> &'static str {
    match s {
        EdgeStatus::Approved => "approved",
        EdgeStatus::Revoked => "revoked",
    }
}
fn request_status(s: RequestStatus) -> &'static str {
    match s {
        RequestStatus::Pending => "pending",
        RequestStatus::Approved => "approved",
        RequestStatus::Rejected => "rejected",
        RequestStatus::Cancelled => "cancelled",
    }
}
fn directed(
    s: &DbAgentInvokeStore,
    c: &AgentInvokeQueryContext,
    d: AgentInvokeDirection,
    kind: &str,
    status: Option<&str>,
) -> (String, Vec<DbValue>) {
    let (anchor, peer) = match d {
        AgentInvokeDirection::Outbound => ("from_id", "to_id"),
        AgentInvokeDirection::Inbound => ("to_id", "from_id"),
    };
    let mut filter = format!(
        "e.env=? AND {}=? AND e.{anchor}=? AND {}=? AND e.{kind}='agent_invoke'",
        s.exact("e.env"),
        s.exact(&format!("e.{anchor}"))
    );
    let mut params = vec![
        s.scope.env.as_str().into(),
        s.scope.env.as_str().into(),
        c.target.bot_id.as_str().into(),
        c.target.bot_id.as_str().into(),
    ];
    if let Some(id) = &c.target.peer_bot_id {
        filter += &format!(" AND e.{peer}=? AND {}=?", s.exact(&format!("e.{peer}")));
        params.extend([id.as_str().into(), id.as_str().into()]);
    }
    if let Some(status) = status {
        filter += " AND e.status=?";
        params.push(status.into());
    }
    (filter, params)
}
fn hint(s: &DbAgentInvokeStore, name: &str) -> String {
    if s.flavor == DbSqlFlavor::Mysql {
        format!("FORCE INDEX ({name})")
    } else {
        format!("INDEXED BY {name}")
    }
}
fn joins(s: &DbAgentInvokeStore) -> String {
    [("f","e.from_id"),("t","e.to_id")].iter().map(|(a,id)|format!(" LEFT JOIN bcs_bots {a} ON {a}.bot_uuid={id} AND {}={} AND {a}.env=e.env AND {}={} AND {a}.actor_kind='bot' AND {}='bot' AND {a}.is_deleted=0",
        s.exact(&format!("{a}.bot_uuid")),s.exact(id),s.exact(&format!("{a}.env")),s.exact("e.env"),s.exact(&format!("{a}.actor_kind")))).collect::<Vec<_>>().join("")
}
pub(super) fn statements(
    s: &DbAgentInvokeStore,
    q: &AgentInvokeQuery,
) -> AgentInvokeResult<Vec<DbStatement>> {
    if matches!(q.context.filter, AgentInvokeQueryFilter::Degrees) {
        let mut result = Vec::new();
        for (direction, pending) in [
            (AgentInvokeDirection::Outbound, false),
            (AgentInvokeDirection::Inbound, false),
            (AgentInvokeDirection::Outbound, true),
            (AgentInvokeDirection::Inbound, true),
        ] {
            result.push(aggregate(s, &q.context, direction, pending));
        }
        return Ok(result);
    }
    if let AgentInvokeQueryFilter::Audit { direction, action } = &q.context.filter {
        let pair = pair(&q.context, *direction).ok_or(AgentInvokeError::InvalidInput)?;
        let index = if action.is_some() {
            "idx_agent_invoke_audit_action_time"
        } else {
            "idx_agent_invoke_audit_time"
        };
        let mut filter = format!(
            "scope_id=? AND from_id=? AND {}=? AND to_id=? AND {}=?",
            s.exact("from_id"),
            s.exact("to_id")
        );
        let mut params = vec![
            P::query_result(0, 0, "id"),
            P::value(pair.from_bot_id.as_str()),
            P::value(pair.from_bot_id.as_str()),
            P::value(pair.to_bot_id.as_str()),
            P::value(pair.to_bot_id.as_str()),
        ];
        if let Some(a) = action {
            filter += " AND action=?";
            params.push(P::value(a.as_str()));
        }
        if let Some(AgentInvokeQueryCursor {
            position: AgentInvokeCursorPosition::AuditTime { created_at_ms, id },
            ..
        }) = q.page.as_ref().and_then(|p| p.cursor.as_ref())
        {
            if s.flavor == DbSqlFlavor::Sqlite {
                filter += " AND (created_at_ms,id)>(?,?)";
                params.extend([P::value(*created_at_ms), P::value(id.get())]);
            } else {
                filter +=
                    " AND created_at_ms>=? AND (created_at_ms>? OR (created_at_ms=? AND id>?))";
                params.extend([
                    P::value(*created_at_ms),
                    P::value(*created_at_ms),
                    P::value(*created_at_ms),
                    P::value(id.get()),
                ]);
            }
        }
        params.push(P::value(
            q.page
                .as_ref()
                .ok_or(AgentInvokeError::InvalidInput)?
                .limit
                .get() as u64
                + 1,
        ));
        return Ok(vec![DbStatement::with_transaction_params(
            format!(
                "SELECT id,action_id,action,principal_kind,principal_id,from_id,to_id,reason,created_at_ms FROM agent_invoke_audit {} WHERE {filter} ORDER BY created_at_ms,id LIMIT ?",
                hint(s, index)
            ),
            params,
        )]);
    }
    let (request, d, status, exact) = match &q.context.filter {
        AgentInvokeQueryFilter::Grant { direction, edge_id } => (
            false,
            *direction,
            None,
            Some(("id", DbValue::from(edge_id.get()))),
        ),
        AgentInvokeQueryFilter::Request {
            direction,
            request_id,
        } => (
            true,
            *direction,
            None,
            Some(("request_id", DbValue::from(request_id.as_str()))),
        ),
        AgentInvokeQueryFilter::Grants { direction, status } => {
            (false, *direction, Some(edge_status(*status)), None)
        }
        AgentInvokeQueryFilter::Requests { direction, status } => {
            (true, *direction, Some(request_status(*status)), None)
        }
        _ => return Err(AgentInvokeError::InvalidInput),
    };
    let table = if request {
        "permission_requests"
    } else {
        "edge_grants"
    };
    let kind = if request {
        "request_kind"
    } else {
        "grant_kind"
    };
    let (mut filter, mut params) = directed(s, &q.context, d, kind, status);
    let index = if request && q.context.target.peer_bot_id.is_some() {
        "idx_agent_invoke_request_pair"
    } else {
        match (request, d) {
            (true, AgentInvokeDirection::Outbound) => "idx_agent_invoke_request_out",
            (true, AgentInvokeDirection::Inbound) => "idx_agent_invoke_request_in",
            (false, AgentInvokeDirection::Outbound) => "idx_agent_invoke_edge_out",
            (false, AgentInvokeDirection::Inbound) => "idx_agent_invoke_edge_in",
        }
    };
    // Exact ID and exact grant-pair lookups may use their existing unique keys.
    let index = if exact.is_some() || (!request && q.context.target.peer_bot_id.is_some()) {
        String::new()
    } else {
        hint(s, index)
    };
    let (fields, metadata, on) = if request {
        (
            "e.id,e.request_id,e.edge_id,e.env,e.from_id,e.to_id,e.request_kind,e.requested_ref_id,e.requested_rules,e.status,e.gmt_create,e.gmt_modified,m.creator_kind",
            "agent_invoke_requests",
            "m.request_id=e.request_id",
        )
    } else {
        (
            "e.id,e.env,e.from_id,e.to_id,e.grant_kind,e.grant_ref_id,e.rules,e.status,e.originator_policy_type,e.originator_policy_data,e.gmt_create,e.gmt_modified,m.created_at_ms,m.updated_at_ms",
            "agent_invoke_grants",
            "m.edge_id=e.id",
        )
    };
    if let Some((key, value)) = exact {
        filter += &format!(" AND e.{key}=? AND {}=?", s.exact(&format!("e.{key}")));
        params.extend([value.clone(), value]);
    }
    if let Some(AgentInvokeQueryCursor {
        position: AgentInvokeCursorPosition::RowId(id),
        ..
    }) = q.page.as_ref().and_then(|p| p.cursor.as_ref())
    {
        filter += " AND e.id>?";
        params.push(id.get().into());
    }
    params.push((q.page.as_ref().map_or(2, |p| p.limit.get() as u64 + 1)).into());
    Ok(vec![DbStatement::with_params(
        format!(
            "SELECT {fields},m.scope_id AS metadata_scope_id,m.version AS metadata_version,f.name AS from_name,f.agent_code AS from_code,t.name AS to_name,t.agent_code AS to_code FROM {table} e {index} LEFT JOIN {metadata} m ON {on} {} WHERE {filter} ORDER BY e.id LIMIT ?",
            joins(s)
        ),
        params,
    )])
}
fn aggregate(
    s: &DbAgentInvokeStore,
    c: &AgentInvokeQueryContext,
    d: AgentInvokeDirection,
    pending: bool,
) -> DbStatement {
    let (table, meta, kind, status, on) = if pending {
        (
            "permission_requests",
            "agent_invoke_requests",
            "request_kind",
            "pending",
            "m.request_id=e.request_id",
        )
    } else {
        (
            "edge_grants",
            "agent_invoke_grants",
            "grant_kind",
            "approved",
            "m.edge_id=e.id",
        )
    };
    let opposite = match d {
        AgentInvokeDirection::Outbound => "e.to_id",
        AgentInvokeDirection::Inbound => "e.from_id",
    };
    let (filter, values) = directed(s, c, d, kind, Some(status));
    let index = if pending && c.target.peer_bot_id.is_some() {
        "idx_agent_invoke_request_pair"
    } else {
        match (pending, d) {
            (true, AgentInvokeDirection::Outbound) => "idx_agent_invoke_request_out",
            (true, AgentInvokeDirection::Inbound) => "idx_agent_invoke_request_in",
            (false, AgentInvokeDirection::Outbound) => "idx_agent_invoke_edge_out",
            (false, AgentInvokeDirection::Inbound) => "idx_agent_invoke_edge_in",
        }
    };
    let canonical = if pending {
        "e.requested_ref_id IS NULL OR e.requested_ref_id<>0 OR e.requested_rules IS NOT NULL OR e.edge_id IS NOT NULL OR m.creator_kind NOT IN ('human','governance')".to_owned()
    } else {
        format!(
            "e.grant_ref_id<>0 OR e.rules IS NOT NULL OR {}<>'same_as_from' OR e.originator_policy_data IS NOT NULL",
            s.exact("e.originator_policy_type")
        )
    };
    let length = if s.flavor == DbSqlFlavor::Mysql {
        "CHAR_LENGTH"
    } else {
        "LENGTH"
    };
    let invalid_pair = format!(
        "{}={} OR LENGTH(TRIM(e.from_id))=0 OR LENGTH(TRIM(e.to_id))=0 OR {length}(e.from_id)>256 OR {length}(e.to_id)>256 OR {}='human_' OR {}='human_'",
        s.exact("e.from_id"),
        s.exact("e.to_id"),
        s.exact("SUBSTR(e.from_id,1,6)"),
        s.exact("SUBSTR(e.to_id,1,6)")
    );
    let bad = format!(
        "m.scope_id IS NULL OR m.scope_id<>? OR m.version<1 OR {}<>'agent_invoke' OR {}<>'{status}' OR {canonical} OR {invalid_pair}",
        s.exact(&format!("e.{kind}")),
        s.exact("e.status")
    );
    let count = if pending {
        "COUNT(*)".into()
    } else {
        format!("COUNT(DISTINCT {})", s.exact(opposite))
    };
    let mut params = vec![P::query_result(0, 0, "id")];
    params.extend(values.into_iter().map(P::Value));
    DbStatement::with_transaction_params(
        format!(
            "SELECT {count} AS n,COALESCE(SUM(CASE WHEN {bad} THEN 1 ELSE 0 END),0) AS bad FROM {table} e {} LEFT JOIN {meta} m ON {on} WHERE {filter}",
            hint(s, index)
        ),
        params,
    )
}
