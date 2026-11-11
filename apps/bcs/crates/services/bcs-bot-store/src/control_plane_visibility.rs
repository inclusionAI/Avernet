//! Privacy normalization at the atomic control-plane storage boundary.
//!
//! Only the active top-level agent scope participates. Approval draft blocks
//! are metadata, not an already-effective publication configuration.

use bcs_db_api::{DbSqlFlavor, DbValue};
use bcs_service_api::{BotControlPlanePatch, FriendCheckInStrategy};
use serde_json::{Map, Value};

const AGENT_SCOPE_KEY: &str = "view_scope_agent_friend_deps";

pub(crate) fn should_normalize(patch: &BotControlPlanePatch) -> bool {
    patch.visibility.is_some()
        || patch.friend_check_in_strategy.is_some()
        || patch.friend_ext.is_some()
}

fn scope_is_empty(friend_ext: &Map<String, Value>) -> bool {
    match friend_ext.get(AGENT_SCOPE_KEY) {
        None | Some(Value::Null) => true,
        Some(Value::Array(scope)) => scope.is_empty(),
        Some(_) => false,
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
/// row. No read/derive/write gap or dependency on SQL SET evaluation order.
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
        None => "COALESCE(friend_check_in_strategy, 'APPROVAL') = 'OPEN'",
    };
    let empty_scope = match patch.friend_ext.as_ref() {
        Some(value) => if scope_is_empty(value) {
            "1 = 1"
        } else {
            "1 = 0"
        }
        .into(),
        None => {
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
    };
    let normalized =
        format!("CASE WHEN ({open}) AND ({empty_scope}) THEN 'public' ELSE 'protected' END");
    let assignment = if patch.visibility.is_some() {
        format!("visibility = {normalized}")
    } else {
        // Keep a concurrently committed private value; also preserve the
        // control-plane read's legacy NULL/empty -> protected default.
        format!(
            "visibility = CASE WHEN COALESCE(NULLIF(visibility, ''), 'protected') \
                 IN ('public', 'protected') THEN {normalized} ELSE visibility END"
        )
    };
    (assignment, Vec::new())
}
