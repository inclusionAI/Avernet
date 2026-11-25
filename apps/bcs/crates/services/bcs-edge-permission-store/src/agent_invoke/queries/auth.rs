use super::*;
use crate::agent_invoke::management_reads::principal_hash;

pub(super) fn statement(s: &DbAgentInvokeStore, c: &AgentInvokeQueryContext) -> DbStatement {
    let mut params: Vec<DbValue> = Vec::new();
    let mut fields = vec!["d.*".to_owned(), format!("{} AS db_now_ms", s.now_ms())];
    let mut bind = |v: DbValue| {
        params.push(v);
        "?"
    };
    let principal = bind(c.principal.id().into());
    fields.push(format!("(SELECT COUNT(*) FROM bcs_bots h WHERE h.bot_uuid={principal} AND {}={} AND h.env=d.env AND {}={} AND h.actor_kind='human' AND {}='human' AND h.is_deleted=0) AS operator_live",
        s.exact("h.bot_uuid"),bind(c.principal.id().into()),s.exact("h.env"),s.exact("d.env"),s.exact("h.actor_kind")));
    // Scalar key lookups avoid materializing another Bot's full record.
    for (name, column, default) in [
        ("anchor_live", "1", "0"),
        ("ownership_version", "b.ownership_version", "0"),
    ] {
        fields.push(format!("COALESCE((SELECT {column} FROM bcs_bots b WHERE b.bot_uuid={} AND {}={} AND b.env=d.env AND {}={} AND b.actor_kind='bot' AND {}='bot' AND b.is_deleted=0 LIMIT 1),{default}) AS {name}",
            bind(c.target.bot_id.as_str().into()),s.exact("b.bot_uuid"),bind(c.target.bot_id.as_str().into()),s.exact("b.env"),s.exact("d.env"),s.exact("b.actor_kind")));
    }
    let canonical = format!(
        "e.grant_ref_id=0 AND e.rules IS NULL AND {}='same_as_from' AND e.originator_policy_data IS NULL AND {}='approved'",
        s.exact("e.originator_policy_type"),
        s.exact("e.status")
    );
    let human = format!(
        "JOIN bcs_bots h ON h.bot_uuid=e.from_id AND {}={} AND h.env=e.env AND {}={} AND h.actor_kind='human' AND {}='human' AND h.is_deleted=0",
        s.exact("h.bot_uuid"),
        s.exact("e.from_id"),
        s.exact("h.env"),
        s.exact("e.env"),
        s.exact("h.actor_kind")
    );
    for (name, join, extra) in [
        ("owner_count", "", String::new()),
        (
            "canonical_owner_count",
            human.as_str(),
            format!(
                " AND {canonical} AND {}='owner' AND {}='human_' AND LENGTH(e.from_id)>6",
                s.exact("e.grant_kind"),
                s.exact("SUBSTR(e.from_id,1,6)")
            ),
        ),
    ] {
        fields.push(format!("(SELECT COUNT(*) FROM edge_grants e {join} WHERE e.to_id={} AND {}={} AND e.env=d.env AND {}={} AND e.grant_kind='owner' AND e.status='approved'{extra}) AS {name}",
            bind(c.target.bot_id.as_str().into()),s.exact("e.to_id"),bind(c.target.bot_id.as_str().into()),s.exact("e.env"),s.exact("d.env")));
    }
    fields.push(format!("(SELECT e.grant_kind FROM edge_grants e {human} WHERE e.to_id={} AND {}={} AND e.from_id={} AND {}={} AND e.env=d.env AND {}={} AND e.status='approved' AND {} IN ('owner','manager') AND {canonical} ORDER BY e.grant_kind DESC LIMIT 1) AS anchor_role",
        bind(c.target.bot_id.as_str().into()),s.exact("e.to_id"),bind(c.target.bot_id.as_str().into()),bind(c.principal.id().into()),s.exact("e.from_id"),bind(c.principal.id().into()),s.exact("e.env"),s.exact("d.env"),s.exact("e.grant_kind")));
    for (name, direction) in [
        (
            "actions",
            c.filter
                .direction()
                .unwrap_or(AgentInvokeDirection::Outbound),
        ),
        ("reverse_actions", AgentInvokeDirection::Inbound),
    ] {
        if matches!(c.principal, AgentInvokePrincipal::Governance(_)) {
            if let Some(pair) = pair(c, direction) {
                fields.push(format!("(SELECT g.actions_json FROM agent_invoke_governance g WHERE g.scope_id=d.id AND g.principal_hash={} AND g.principal_id={} AND {}={} AND g.from_id={} AND {}={} AND g.to_id={} AND {}={} LIMIT 1) AS {name}",
                    bind(principal_hash(&c.principal).into()),bind(c.principal.id().into()),s.exact("g.principal_id"),bind(c.principal.id().into()),
                    bind(pair.from_bot_id.as_str().into()),s.exact("g.from_id"),bind(pair.from_bot_id.as_str().into()),bind(pair.to_bot_id.as_str().into()),s.exact("g.to_id"),bind(pair.to_bot_id.as_str().into())));
                continue;
            }
        }
        fields.push(format!("NULL AS {name}"));
    }
    let sql = format!(
        "SELECT {} FROM agent_invoke_scopes d WHERE d.namespace={} AND {}={} AND d.env={} AND {}={} LIMIT 2",
        fields.join(","),
        bind(s.scope.namespace.as_str().into()),
        s.exact("d.namespace"),
        bind(s.scope.namespace.as_str().into()),
        bind(s.scope.env.as_str().into()),
        s.exact("d.env"),
        bind(s.scope.env.as_str().into())
    );
    DbStatement::with_params(sql, params)
}
fn actions(row: &DbRow, key: &str) -> AgentInvokeResult<Vec<AgentInvokeAction>> {
    if row.get(key) == Some(&DbValue::Null) {
        return Ok(Vec::new());
    }
    let names: Vec<String> =
        serde_json::from_str(&string(row, key)?).map_err(|_| AgentInvokeError::InvalidState)?;
    let mut result = Vec::new();
    for name in names {
        let a = match name.as_str() {
            "request" => AgentInvokeAction::Request,
            "grant" => AgentInvokeAction::Grant,
            "approve" => AgentInvokeAction::Approve,
            "reject" => AgentInvokeAction::Reject,
            "cancel" => AgentInvokeAction::Cancel,
            "revoke" => AgentInvokeAction::Revoke,
            "query" => AgentInvokeAction::Query,
            _ => return Err(AgentInvokeError::InvalidState),
        };
        if result.contains(&a) {
            return Err(AgentInvokeError::InvalidState);
        }
        result.push(a);
    }
    Ok(result)
}
pub(super) fn decode(
    s: &DbAgentInvokeStore,
    c: &AgentInvokeQueryContext,
    rows: &[DbRow],
) -> AgentInvokeResult<AgentInvokeQueryFacts> {
    s.decode_scope_rows(rows)?;
    let [row] = rows else {
        return Err(AgentInvokeError::InvalidState);
    };
    let anchor_role = if row.get("anchor_role") == Some(&DbValue::Null) {
        None
    } else {
        Some(match string(row, "anchor_role")?.as_str() {
            "owner" => AgentInvokeRole::Owner,
            "manager" => AgentInvokeRole::Manager,
            _ => return Err(AgentInvokeError::InvalidState),
        })
    };
    Ok(AgentInvokeQueryFacts {
        context: c.clone(),
        operator_live: boolean(row, "operator_live")?,
        anchor_live: boolean(row, "anchor_live")?,
        ownership_version: number(row, "ownership_version")?,
        owner_count: number(row, "owner_count")?,
        canonical_owner_count: number(row, "canonical_owner_count")?,
        anchor_role,
        delegated_actions: actions(row, "actions")?,
        reverse_delegated_actions: actions(row, "reverse_actions")?,
    })
}
