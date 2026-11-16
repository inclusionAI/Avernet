//! Privacy normalization at the atomic control-plane storage boundary.
//!
//! Only the active top-level agent scope participates. Approval draft blocks
//! are metadata, not an already-effective publication configuration.

use bcs_db_api::{DbSqlFlavor, DbValue};
use bcs_service_api::{BotControlPlanePatch, FriendCheckInStrategy};
use serde_json::{Map, Value};

pub(crate) const AGENT_SCOPE_KEY: &str = "view_scope_agent_friend_deps";

pub(crate) fn should_normalize(patch: &BotControlPlanePatch) -> bool {
    patch.visibility.is_some()
        || patch.friend_check_in_strategy.is_some()
        || patch.friend_ext.is_some()
}

/// Legacy external clients send only visibility=public for all-visible intent.
/// An explicitly supplied friend_ext or strategy is authoritative.
pub(crate) fn opens_check_in_for_legacy_all_visible(patch: &BotControlPlanePatch) -> bool {
    patch.visibility.as_deref() == Some("public")
        && patch.friend_ext.is_none()
        && patch.friend_check_in_strategy.is_none()
}

pub(crate) fn clears_agent_scope(patch: &BotControlPlanePatch) -> bool {
    patch.visibility.as_deref() == Some("public")
        && patch.friend_ext.is_none()
        && patch
            .friend_check_in_strategy
            .is_none_or(|strategy| strategy == FriendCheckInStrategy::Open)
}

fn scope_is_empty(friend_ext: &Map<String, Value>) -> bool {
    match friend_ext.get(AGENT_SCOPE_KEY) {
        None | Some(Value::Null) => true,
        Some(Value::Array(scope)) => scope.is_empty(),
        Some(_) => false,
    }
}

pub(crate) fn should_normalize_scope_transition(
    patch: &BotControlPlanePatch,
    current_friend_ext: &Map<String, Value>,
) -> bool {
    if patch.visibility.is_some() || patch.friend_check_in_strategy.is_some() {
        return true;
    }
    patch.friend_ext.as_ref().is_some_and(|next| {
        scope_is_empty(current_friend_ext) != scope_is_empty(next)
    })
}

fn sql_live_scope_is_empty(flavor: DbSqlFlavor) -> String {
    let path = format!("'$.{AGENT_SCOPE_KEY}'");
    match flavor {
        DbSqlFlavor::Sqlite => format!(
            "(COALESCE(json_type(friend_ext, {path}), 'null') = 'null' OR \
             (json_type(friend_ext, {path}) = 'array' AND json_array_length(friend_ext, {path}) = 0))"
        ),
        DbSqlFlavor::Mysql => format!(
            "(COALESCE(JSON_TYPE(JSON_EXTRACT(friend_ext, {path})), 'NULL') = 'NULL' OR \
             (JSON_TYPE(JSON_EXTRACT(friend_ext, {path})) = 'ARRAY' AND JSON_LENGTH(friend_ext, {path}) = 0))"
        ),
    }
}

pub(crate) fn normalize(
    visibility: &str,
    strategy: FriendCheckInStrategy,
    friend_ext: &Map<String, Value>,
) -> String {
    match visibility {
        "public" | "protected" | "" => {
            if strategy == FriendCheckInStrategy::Open && scope_is_empty(friend_ext) {
                "public".into()
            } else {
                "protected".into()
            }
        }
        // In particular, never make a private Bot visible just by opening its
        // check-in strategy. Invalid values keep their existing validation path.
        other => other.into(),
    }
}

/// Build one assignment using patch fields when present, otherwise the live
/// row. There is no read/derive/write gap. The caller must emit this visibility
/// assignment before any friend_ext assignment: MySQL evaluates SET from left
/// to right, and scope_transition must read the pre-update live friend_ext.
pub(crate) fn sql_assignment(
    patch: &BotControlPlanePatch,
    flavor: DbSqlFlavor,
) -> (String, Vec<DbValue>) {
    if let Some(value) = patch.visibility.as_deref()
        && !matches!(value, "public" | "protected" | "")
    {
        return ("visibility = ?".into(), vec![DbValue::from(value)]);
    }
    let open = match patch.friend_check_in_strategy {
        Some(FriendCheckInStrategy::Open) => "1 = 1",
        Some(_) => "1 = 0",
        None if opens_check_in_for_legacy_all_visible(patch) => "1 = 1",
        None => "COALESCE(friend_check_in_strategy, 'APPROVAL') = 'OPEN'",
    };
    let empty_scope = match patch.friend_ext.as_ref() {
        Some(value) => if scope_is_empty(value) {
            "1 = 1"
        } else {
            "1 = 0"
        }
        .into(),
        None if clears_agent_scope(patch) => "1 = 1".into(),
        None => sql_live_scope_is_empty(flavor),
    };
    let normalized =
        format!("CASE WHEN ({open}) AND ({empty_scope}) THEN 'public' ELSE 'protected' END");
    let scope_transition = if patch.visibility.is_none()
        && patch.friend_check_in_strategy.is_none()
        && let Some(next) = patch.friend_ext.as_ref()
    {
        let live_empty = sql_live_scope_is_empty(flavor);
        if scope_is_empty(next) {
            format!("NOT ({live_empty})")
        } else {
            live_empty
        }
    } else {
        "1 = 1".to_string()
    };
    let assignment = if patch.visibility.is_some() {
        format!("visibility = {normalized}")
    } else {
        // Keep a concurrently committed private value; also preserve the
        // control-plane read's legacy NULL/empty -> protected default. A
        // metadata-only friend_ext write normalizes only when the live and
        // target scope categories differ.
        format!(
            "visibility = CASE WHEN ({scope_transition}) AND \
                 COALESCE(NULLIF(visibility, ''), 'protected') \
                 IN ('public', 'protected') THEN {normalized} ELSE visibility END"
        )
    };
    (assignment, Vec::new())
}
