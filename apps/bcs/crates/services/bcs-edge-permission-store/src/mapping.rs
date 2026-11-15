//! SQL row mapping and shared statement helpers.
use super::*;

pub(super) fn row_to_permission_request(row: &DbRow) -> ServiceResult<PermissionRequest> {
    // decided_at is a DB-managed timestamp (TEXT/`timestamp NULL`); parse the
    // stored instant back to epoch ms. `None` ⇒ not yet decided.
    let decided_at = optional_timestamp_text(row, "decided_at")?
        .and_then(|s| parse_timestamp_epoch_ms(&s));
    Ok(PermissionRequest {
        request_id: required_string(row, "request_id")?,
        edge_id: optional_u64(row, "edge_id")?,
        env: required_string(row, "env")?,
        from_id: required_string(row, "from_id")?,
        to_id: required_string(row, "to_id")?,
        request_kind: parse_request_kind(&required_string(row, "request_kind")?)?,
        requested_ref_id: optional_u64(row, "requested_ref_id")?,
        requested_rules: parse_json_opt(&optional_string(row, "requested_rules")?)?,
        message: optional_string(row, "message")?,
        status: parse_request_status(&required_string(row, "status")?)?,
        decision_reason: optional_string(row, "decision_reason")?,
        created_by: required_string(row, "created_by")?,
        decided_by: optional_string(row, "decided_by")?,
        decided_at,
        created_at: required_timestamp_epoch_ms(row, "gmt_create")?,
        updated_at: required_timestamp_epoch_ms(row, "gmt_modified")?,
    })
}

pub(super) fn parse_request_kind(value: &str) -> ServiceResult<RequestKind> {
    match value {
        "connect" => Ok(RequestKind::Connect),
        "permission_profile" => Ok(RequestKind::PermissionProfile),
        "rules" => Ok(RequestKind::Rules),
        "revoke" => Ok(RequestKind::Revoke),
        other => Err(ServiceError::InternalError(format!(
            "unknown request_kind: {}",
            other
        ))),
    }
}

pub(super) fn parse_request_status(value: &str) -> ServiceResult<RequestStatus> {
    match value {
        "pending" => Ok(RequestStatus::Pending),
        "approved" => Ok(RequestStatus::Approved),
        "rejected" => Ok(RequestStatus::Rejected),
        "cancelled" => Ok(RequestStatus::Cancelled),
        other => Err(ServiceError::InternalError(format!(
            "unknown request status: {}",
            other
        ))),
    }
}

pub(super) fn request_kind_str(kind: RequestKind) -> &'static str {
    match kind {
        RequestKind::Connect => "connect",
        RequestKind::PermissionProfile => "permission_profile",
        RequestKind::Rules => "rules",
        RequestKind::Revoke => "revoke",
    }
}

pub(super) fn request_status_str(status: RequestStatus) -> &'static str {
    match status {
        RequestStatus::Pending => "pending",
        RequestStatus::Approved => "approved",
        RequestStatus::Rejected => "rejected",
        RequestStatus::Cancelled => "cancelled",
    }
}

pub(super) fn row_to_permission_profile(row: &DbRow) -> ServiceResult<PermissionProfile> {
    let revision = row
        .get_i64("revision")
        .map_err(|err| service_db_error("revision", err))?
        .unwrap_or(0) as u64;
    let is_default = row
        .get_i64("is_default")
        .map_err(|err| service_db_error("is_default", err))?
        .unwrap_or(0)
        != 0;
    // rules_template is NOT NULL in DDL, so it is always present; parse string→Value.
    let rules_template = match required_string(row, "rules_template")? {
        ref s if s.is_empty() => serde_json::Value::Null,
        ref s => serde_json::from_str::<serde_json::Value>(s).map_err(|err| {
            ServiceError::InternalError(format!("permission_profiles json parse: {}", err))
        })?,
    };
    Ok(PermissionProfile {
        permission_profile_id: required_u64(row, "id")?,
        bot_id: required_string(row, "bot_id")?,
        env: required_string(row, "env")?,
        name: required_string(row, "name")?,
        description: optional_string(row, "description")?,
        rules_template,
        revision,
        digest: required_string(row, "digest")?,
        is_default,
        status: parse_profile_status(&required_string(row, "status")?)?,
        created_by: required_string(row, "created_by")?,
        updated_by: optional_string(row, "updated_by")?,
    })
}

pub(super) fn row_to_edge_grant(row: &DbRow) -> ServiceResult<EdgeGrant> {
    Ok(EdgeGrant {
        edge_id: required_u64(row, "id")?,
        env: required_string(row, "env")?,
        from_id: required_string(row, "from_id")?,
        to_id: required_string(row, "to_id")?,
        grant_kind: parse_grant_kind(&required_string(row, "grant_kind")?)?,
        grant_ref_id: required_u64(row, "grant_ref_id")?,
        rules: parse_json_opt(&optional_string(row, "rules")?)?,
        status: parse_edge_status(&required_string(row, "status")?)?,
        originator_policy_type: parse_originator_policy_type(
            &required_string(row, "originator_policy_type")?,
        )?,
        originator_policy_data: parse_json_opt(&optional_string(row, "originator_policy_data")?)?,
    })
}

/// Map a `bcs_bots` row to a [`BotActorConfig`].
///
/// `created_by` is `NULL` for legacy bots. `user_visibility` is read from its
/// own `bcs_bots.user_visibility` column — the same column the internal-
/// attributes PATCH writes — so friend-gating sees the value the operator
/// actually set.
/// `friend_check_in_strategy` is read from its dedicated column so the
/// friend-connect policy does not depend on `bot_info` JSON projection order.
/// `friend_ext` is read from the dedicated `bcs_bots.friend_ext` column, with
/// a legacy `bot_info.friend_ext` fallback for older rows.
pub(super) fn row_to_bot_actor_config(row: &DbRow) -> ServiceResult<BotActorConfig> {
    let bot_info = optional_string(row, "bot_info")?
        .and_then(|value| serde_json::from_str::<serde_json::Value>(&value).ok())
        .unwrap_or_default();
    let user_visibility = optional_string(row, "user_visibility")?
        .unwrap_or_else(|| "protected".to_string());
    let friend_check_in_strategy = optional_string(row, "friend_check_in_strategy")?
        .unwrap_or_else(|| "APPROVAL".to_string());
    let friend_ext = optional_string(row, "friend_ext")?
        .and_then(|value| serde_json::from_str::<serde_json::Value>(&value).ok())
        .and_then(|value| value.as_object().cloned())
        .or_else(|| {
            bot_info
                .get("friend_ext")
                .and_then(|value| value.as_object().cloned())
        })
        .unwrap_or_default();
    Ok(BotActorConfig {
        bot_id: required_string(row, "bot_uuid")?,
        env: required_string(row, "env")?,
        name: required_string(row, "name")?,
        visibility: required_string(row, "visibility")?,
        status: required_string(row, "status")?,
        created_by: optional_string(row, "created_by")?,
        user_visibility,
        friend_check_in_strategy,
        friend_ext,
    })
}

pub(super) fn required_string(row: &DbRow, column: &'static str) -> ServiceResult<String> {
    optional_string(row, column)?.ok_or_else(|| {
        ServiceError::InternalError(format!("missing edge_grants column {}", column))
    })
}

pub(super) fn optional_string(row: &DbRow, column: &'static str) -> ServiceResult<Option<String>> {
    match row.get(column) {
        None | Some(DbValue::Null) => Ok(None),
        Some(DbValue::String(value)) => Ok(Some(value.clone())),
        Some(DbValue::Bytes(value)) => String::from_utf8(value.clone())
            .map(Some)
            .map_err(|err| {
                service_db_error(
                    column,
                    DbError::Conversion(format!(
                        "column '{}' is not valid UTF-8: {}",
                        column, err
                    )),
                )
            }),
        Some(other) => Err(service_db_error(
            column,
            DbError::Conversion(format!(
                "column '{}' is not a string: {:?}",
                column, other
            )),
        )),
    }
}

pub(super) fn optional_timestamp_text(row: &DbRow, column: &'static str) -> ServiceResult<Option<String>> {
    match row.get(column) {
        None | Some(DbValue::Null) => Ok(None),
        Some(DbValue::String(value)) => Ok(Some(value.clone())),
        Some(DbValue::Bytes(value)) => String::from_utf8(value.clone())
            .map(Some)
            .map_err(|err| service_db_error(column, DbError::Conversion(format!("column '{}' is not valid UTF-8: {}", column, err)))),
        Some(other) => Err(service_db_error(column, DbError::Conversion(format!("column '{}' is not a timestamp string: {:?}", column, other)))),
    }
}

/// Parse a required, DB-managed timestamp column to epoch milliseconds.
///
/// `gmt_create`/`gmt_modified` are NOT NULL in the schema; a missing or
/// unparseable value is an internal data error, not an absent optional field.
pub(super) fn required_timestamp_epoch_ms(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    let text = optional_timestamp_text(row, column)?
        .ok_or_else(|| ServiceError::InternalError(format!("missing edge_permission column {}", column)))?;
    parse_timestamp_epoch_ms(&text).ok_or_else(|| {
        ServiceError::InternalError(format!(
            "invalid edge_permission timestamp {}: {}",
            column, text
        ))
    })
}

pub(super) fn required_u64(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    let value = row
        .get_i64(column)
        .map_err(|err| service_db_error(column, err))?
        .ok_or_else(|| ServiceError::InternalError(format!("missing edge_permission column {}", column)))?;
    if value < 0 {
        return Err(ServiceError::InternalError(format!("negative edge_permission column {}", column)));
    }
    Ok(value as u64)
}

pub(super) fn optional_u64(row: &DbRow, column: &'static str) -> ServiceResult<Option<u64>> {
    Ok(row
        .get_i64(column)
        .map_err(|err| service_db_error(column, err))?
        .and_then(|value| if value < 0 { None } else { Some(value as u64) }))
}

pub(super) fn parse_json_opt(value: &Option<String>) -> ServiceResult<Option<serde_json::Value>> {
    match value {
        None => Ok(None),
        Some(s) if s.is_empty() => Ok(None),
        Some(s) => serde_json::from_str::<serde_json::Value>(s).map(Some).map_err(|err| {
            ServiceError::InternalError(format!("edge_grants json parse: {}", err))
        }),
    }
}

pub(super) fn parse_grant_kind(value: &str) -> ServiceResult<GrantKind> {
    match value {
        "permission_profile" => Ok(GrantKind::PermissionProfile),
        "rules" => Ok(GrantKind::Rules),
        "owner" => Ok(GrantKind::Owner),
        "manager" => Ok(GrantKind::Manager),
        other => Err(ServiceError::InternalError(format!(
            "unknown grant_kind: {}",
            other
        ))),
    }
}

/// Parse a DB-managed timestamp back to epoch milliseconds (UTC).
///
/// Accepts `YYYY-MM-DD HH:MM:SS` (SQLite `CURRENT_TIMESTAMP` / MySQL `timestamp`)
/// and the ISO `T` separator variant, with optional fractional seconds. Returns
/// `None` for an unparseable/empty value (e.g. NULL ⇒ already `None` upstream).
pub(super) fn parse_timestamp_epoch_ms(value: &str) -> Option<u64> {
    let s = value.trim();
    if s.is_empty() {
        return None;
    }
    let s = s.replacen('T', " ", 1);
    let mut parts = s.split(' ');
    let date = parts.next()?;
    let time = parts.next()?;
    let d: Vec<&str> = date.split('-').collect();
    if d.len() != 3 {
        return None;
    }
    let t_main = time.split('.').next()?;
    let t: Vec<&str> = t_main.split(':').collect();
    if t.len() != 3 {
        return None;
    }
    let (y, mo, dy) = (d[0].parse::<i64>().ok()?, d[1].parse::<i64>().ok()?, d[2].parse::<i64>().ok()?);
    let (h, mi, se) = (t[0].parse::<u64>().ok()?, t[1].parse::<u64>().ok()?, t[2].parse::<u64>().ok()?);
    if !(1..=12).contains(&mo) || !(1..=31).contains(&dy) {
        return None;
    }
    // Howard Hinnant's days_from_civil — counts days since 1970-01-01 (UTC).
    let y = if mo <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = (y - era * 400) as u64;
    let doy = ((153 * (if mo > 2 { mo - 3 } else { mo + 9 }) + 2) / 5 + dy - 1) as u64;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = (era * 146097 + doe as i64 - 719468) as i64;
    if days < 0 {
        return None;
    }
    let epoch_ms = (days as u64) * 86_400_000 + h * 3_600_000 + mi * 60_000 + se * 1_000;
    Some(epoch_ms)
}

pub(super) fn parse_edge_status(value: &str) -> ServiceResult<EdgeStatus> {
    match value {
        "approved" => Ok(EdgeStatus::Approved),
        "revoked" => Ok(EdgeStatus::Revoked),
        other => Err(ServiceError::InternalError(format!(
            "unknown edge status: {}",
            other
        ))),
    }
}

pub(super) fn parse_originator_policy_type(value: &str) -> ServiceResult<OriginatorPolicyType> {
    match value {
        "any" => Ok(OriginatorPolicyType::Any),
        "same_as_from" => Ok(OriginatorPolicyType::SameAsFrom),
        "specific" => Ok(OriginatorPolicyType::Specific),
        "owner" => Ok(OriginatorPolicyType::Owner),
        other => Err(ServiceError::InternalError(format!(
            "unknown originator_policy_type: {}",
            other
        ))),
    }
}

pub(super) fn grant_kind_str(kind: GrantKind) -> &'static str {
    match kind {
        GrantKind::PermissionProfile => "permission_profile",
        GrantKind::Rules => "rules",
        GrantKind::Owner => "owner",
        GrantKind::Manager => "manager",
    }
}

pub(super) fn edge_status_str(status: EdgeStatus) -> &'static str {
    match status {
        EdgeStatus::Approved => "approved",
        EdgeStatus::Revoked => "revoked",
    }
}

pub(super) fn originator_policy_type_str(policy: OriginatorPolicyType) -> &'static str {
    match policy {
        OriginatorPolicyType::Any => "any",
        OriginatorPolicyType::SameAsFrom => "same_as_from",
        OriginatorPolicyType::Specific => "specific",
        OriginatorPolicyType::Owner => "owner",
    }
}

pub(super) fn parse_profile_status(value: &str) -> ServiceResult<ProfileStatus> {
    match value {
        "active" => Ok(ProfileStatus::Active),
        "deleted" => Ok(ProfileStatus::Deleted),
        other => Err(ServiceError::InternalError(format!(
            "unknown profile status: {}",
            other
        ))),
    }
}

/// Wildcard-allow rules template (default profile seed, spec §5.1.1).
pub(super) const WILDCARD_ALLOW: &str = r#"[{"tool":"*","specifier":"*","effect":"allow"}]"#;

/// SHA-256 hex digest of a string (used for `permission_profiles.digest`).
pub(super) fn sha256_hex(input: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(input.as_bytes());
    hex_encode(&hasher.finalize())
}

/// Lowercase hex encoding without pulling in an extra dependency.
pub(super) fn hex_encode(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push_str(&format!("{:02x}", byte));
    }
    out
}

pub(super) fn json_to_db_value(value: &Option<serde_json::Value>) -> DbValue {
    match value {
        None => DbValue::Null,
        Some(v) => match serde_json::to_string(v) {
            Ok(s) => DbValue::from(s),
            Err(err) => {
                warn!(error = %err, "edge_grants: failed to serialize json, storing NULL");
                DbValue::Null
            }
        },
    }
}

/// A single read returns count + page from the same statement snapshot, even
/// beyond the last page. Only the requested IDs cross the DB plugin boundary.
pub(super) fn friend_list_statement(
    flavor: EdgeGrantSqlFlavor,
    actor: &str,
    env: &str,
    query: FriendListQuery,
) -> ServiceResult<DbStatement> {
    if query.limit == 0 || query.limit > 100 || query.offset > i64::MAX as u64 {
        return Err(ServiceError::InvalidOperation {
            message: "invalid friend pagination bounds".into(), request_id: None,
        });
    }
    // Binary comparison preserves Rust String ordering and HashSet identity,
    // independent of MySQL's default case-insensitive database collation.
    let (outbound_id, inbound_id, prefix) = match flavor {
        EdgeGrantSqlFlavor::Mysql => (
            "CAST(g.to_id AS BINARY)", "CAST(g.from_id AS BINARY)",
            "SUBSTR(peer_id, 1, 6) = CAST('human_' AS BINARY)",
        ),
        EdgeGrantSqlFlavor::Sqlite => (
            "g.to_id COLLATE BINARY", "g.from_id COLLATE BINARY",
            "SUBSTR(peer_id, 1, 6) COLLATE BINARY = 'human_'",
        ),
    };
    let filter = match query.target_type {
        None => "1 = 1".to_string(),
        Some(ActorKind::Human) => prefix.to_string(),
        Some(ActorKind::Bot) => format!("NOT ({prefix})"),
    };
    let sql = format!(
        "WITH friend_ids AS (
           SELECT {outbound_id} AS peer_id FROM edge_grants g
           JOIN permission_profiles p ON p.id = g.grant_ref_id
             AND p.bot_id = g.to_id AND p.env = g.env
             AND p.is_default = 1 AND p.status = 'active'
           WHERE g.from_id = ? AND g.env = ? AND g.status = 'approved'
             AND g.grant_kind = 'permission_profile'
           UNION
           SELECT {inbound_id} AS peer_id FROM edge_grants g
           JOIN permission_profiles p ON p.id = g.grant_ref_id
             AND p.bot_id = g.to_id AND p.env = g.env
             AND p.is_default = 1 AND p.status = 'active'
           WHERE g.to_id = ? AND g.env = ? AND g.status = 'approved'
             AND g.grant_kind = 'permission_profile'
         ), filtered AS (SELECT peer_id FROM friend_ids WHERE {filter})
         SELECT totals.total, page.peer_id
         FROM (SELECT COUNT(*) AS total FROM filtered) totals
         LEFT JOIN (SELECT peer_id FROM filtered ORDER BY peer_id LIMIT ? OFFSET ?) page
           ON 1 = 1 ORDER BY page.peer_id"
    );
    Ok(DbStatement::with_params(sql, vec![
        DbValue::from(actor), DbValue::from(env),
        DbValue::from(actor), DbValue::from(env),
        DbValue::from(u64::from(query.limit)), DbValue::from(query.offset),
    ]))
}

pub(super) fn service_db_error(operation: &'static str, err: DbError) -> ServiceError {
    ServiceError::InternalError(format!("edge_grants db {}: {}", operation, err))
}
