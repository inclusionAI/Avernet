//! Strict decode of authority role rows (spec §5.1) — fail closed, never
//! serde-default a corrupted source into a valid role.
//!
//! The decode reuses the domain helpers (`bcs_domain::decode_role_source`,
//! the fixed `ROLE_GRANT_REF_ID`) so the storage codec and the domain encode
//! one vocabulary. Any illegal shape is mapped to the
//! `AuthorityError::CorruptAuthority` business branch (an infrastructure
//! value error is an `InternalError`, not a role decision).

use bcs_db_api::DbRow;
use bcs_domain::{
    decode_role_source, BotAccessRelation, DecodedRoleSource, OwnershipState, ROLE_GRANT_REF_ID,
};
use bcs_service_api::port::repo::bot_authority::user_id_from_actor;
use bcs_service_api::types::error::AuthorityError;
use bcs_service_api::{ServiceError, ServiceResult};

use crate::common::{optional_string, required_string, required_u64};

/// Columns the strict role read selects; shared by [`relation_from_row`] and
/// the ownership owner-row validation so the shape is stated once.
pub(super) const ROLE_ROW_COLUMNS: &'static str =
    "id, env, from_id, to_id, grant_kind, grant_ref_id, rules, status, \
     originator_policy_type, management_source_kind, management_source_id";

/// Decode one role row into its [`BotAccessRelation`], validating the full
/// role shape (spec §5.1): `grant_ref_id = 0`, no inline rules, and the
/// (kind, source) pair consistent through the domain decode.
pub(super) fn relation_from_row(
    row: &DbRow,
    bot_id: &str,
    env: &str,
) -> ServiceResult<BotAccessRelation> {
    let grant_kind = required_string(row, "grant_kind")?;
    let grant_ref_id = required_u64(row, "grant_ref_id")?;
    if grant_ref_id != ROLE_GRANT_REF_ID {
        return Err(corrupt(bot_id, env, &format!(
            "role row with grant_ref_id {grant_ref_id} (expected {ROLE_GRANT_REF_ID})"
        )));
    }
    // Role edges carry no inline rules (the CHECK keeps NULL; the strict read
    // enforces it for defense in depth).
    if optional_string(row, "rules")?.is_some() {
        return Err(corrupt(bot_id, env, "role row carries inline rules"));
    }
    let source_kind = required_string(row, "management_source_kind")?;
    let source_id = required_string(row, "management_source_id")?;
    let decoded = decode_role_source(&source_kind, &source_id).map_err(|err| {
        corrupt(bot_id, env, &format!("undecodable management source: {}", err))
    })?;
    match (grant_kind.as_str(), decoded) {
        ("owner", DecodedRoleSource::Owner) => Ok(BotAccessRelation::Owner),
        ("manager", DecodedRoleSource::Manager(_)) => Ok(BotAccessRelation::Manager),
        (other, _) => Err(corrupt(
            bot_id,
            env,
            &format!("role kind '{other}' inconsistent with its management source"),
        )),
    }
}

/// Build the ownership snapshot from THE single approved owner row, also
/// validating that its `from_id` is in the Human actor id shape (a role edge
/// is Human → physical Bot, so a non-`human_<uid>` owner edge is corrupt).
pub(super) fn ownership_state_from_owner_row(
    row: &DbRow,
    bot_id: &str,
    env: &str,
    ownership_version: u64,
) -> ServiceResult<OwnershipState> {
    let from_id = required_string(row, "from_id")?;
    let owner_user_id = user_id_from_actor(&from_id)
        .ok_or_else(|| {
            corrupt(bot_id, env, &format!("owner edge from non-human actor id '{from_id}'"))
        })?
        .to_string();
    // The row itself must also decode as a legal owner edge.
    relation_from_row(row, bot_id, env)?;
    Ok(OwnershipState {
        owner_user_id,
        ownership_version,
    })
}

fn corrupt(bot_id: &str, env: &str, detail: &str) -> ServiceError {
    ServiceError::Authority(AuthorityError::CorruptAuthority {
        bot_id: bot_id.to_string(),
        env: env.to_string(),
        detail: detail.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    use bcs_db_api::DbValue;
    use bcs_domain::{DIRECT_SOURCE_ID, DIRECT_SOURCE_KIND, OWNER_SOURCE_ID, OWNER_SOURCE_KIND};
    use bcs_service_api::types::error::AuthorityError;

    use super::*;

    const ENV: &str = "local";

    fn row(kind: &str, source_kind: &str, source_id: &str, ref_id: i64, rules: Option<&str>) -> DbRow {
        let mut columns = BTreeMap::new();
        columns.insert("id".to_string(), DbValue::from(1_i64));
        columns.insert("env".to_string(), DbValue::from(ENV));
        columns.insert("from_id".to_string(), DbValue::from("human_user-a"));
        columns.insert("to_id".to_string(), DbValue::from("bot-a"));
        columns.insert("grant_kind".to_string(), DbValue::from(kind));
        columns.insert("grant_ref_id".to_string(), DbValue::from(ref_id));
        match rules {
            Some(text) => columns.insert("rules".to_string(), DbValue::from(text)),
            None => columns.insert("rules".to_string(), DbValue::Null),
        };
        columns.insert("status".to_string(), DbValue::from("approved"));
        columns.insert("originator_policy_type".to_string(), DbValue::from("same_as_from"));
        columns.insert("management_source_kind".to_string(), DbValue::from(source_kind));
        columns.insert("management_source_id".to_string(), DbValue::from(source_id));
        DbRow::new(columns)
    }

    #[test]
    fn decodes_owner_and_manager_rows_strictly() {
        let owner = row("owner", OWNER_SOURCE_KIND, OWNER_SOURCE_ID, 0, None);
        assert_eq!(
            relation_from_row(&owner, "bot-a", ENV).unwrap(),
            BotAccessRelation::Owner
        );
        let state = ownership_state_from_owner_row(&owner, "bot-a", ENV, 3).unwrap();
        assert_eq!(state.owner_user_id, "user-a");
        assert_eq!(state.ownership_version, 3);
        let manager = row("manager", DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID, 0, None);
        assert_eq!(
            relation_from_row(&manager, "bot-a", ENV).unwrap(),
            BotAccessRelation::Manager
        );
        let team = row("manager", "team", "team-a", 0, None);
        assert_eq!(
            relation_from_row(&team, "bot-a", ENV).unwrap(),
            BotAccessRelation::Manager
        );
    }

    #[test]
    fn illegal_shapes_fail_closed_never_default_into_roles() {
        let cases = [
            // Owner shape violations.
            row("owner", DIRECT_SOURCE_KIND, DIRECT_SOURCE_ID, 0, None),
            row("owner", OWNER_SOURCE_KIND, OWNER_SOURCE_ID, 1, None),
            row("owner", OWNER_SOURCE_KIND, OWNER_SOURCE_ID, 0, Some("[{}]")),
            // Manager shape violations.
            row("manager", OWNER_SOURCE_KIND, OWNER_SOURCE_ID, 0, None),
            row("manager", "direct", "not-manual", 0, None),
            row("manager", "team", "", 0, None),
            row("manager", "direct", "manual", 9, None),
            row("manager", "direct", "manual", 0, Some("[{}]")),
            // Unknown kinds/sources never decode.
            row("banana", OWNER_SOURCE_KIND, OWNER_SOURCE_ID, 0, None),
            row("manager", "runtime", "legacy", 0, None),
        ];
        for corrupt_row in cases {
            let result = relation_from_row(&corrupt_row, "bot-a", ENV);
            assert!(
                matches!(result, Err(ServiceError::Authority(
                    AuthorityError::CorruptAuthority { .. }
                ))),
                "corrupt row must fail closed: {:?}",
                corrupt_row.get("grant_kind")
            );
        }
    }

    #[test]
    fn ownership_rejects_non_human_owner_actor_ids() {
        let mut columns = BTreeMap::new();
        columns.insert("id".to_string(), DbValue::from(1_i64));
        columns.insert("env".to_string(), DbValue::from(ENV));
        // A Bot-shaped from_id is not a legal owner edge subject.
        columns.insert("from_id".to_string(), DbValue::from("x:85020"));
        columns.insert("to_id".to_string(), DbValue::from("bot-a"));
        columns.insert("grant_kind".to_string(), DbValue::from("owner"));
        columns.insert("grant_ref_id".to_string(), DbValue::from(0_i64));
        columns.insert("rules".to_string(), DbValue::Null);
        columns.insert("status".to_string(), DbValue::from("approved"));
        columns.insert("originator_policy_type".to_string(), DbValue::from("same_as_from"));
        columns.insert("management_source_kind".to_string(), DbValue::from(OWNER_SOURCE_KIND));
        columns.insert("management_source_id".to_string(), DbValue::from(OWNER_SOURCE_ID));
        let result = ownership_state_from_owner_row(&DbRow::new(columns), "bot-a", ENV, 1);
        assert!(matches!(
            result,
            Err(ServiceError::Authority(AuthorityError::CorruptAuthority { .. }))
        ));
    }
}