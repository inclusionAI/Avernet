//! Shared Group row projection and JOIN participant aggregation for the SQL store.
//!
//! Every JOIN entry point decodes the same Group columns; only eight column
//! names differ between the ordinary projection and the participant-scoped
//! find projection. Callers select one fixed set and this module never probes
//! for alternatives.
//!
//! The strictly-decoded member-list projector in
//! `store_reads.rs::load_participants_from_mysql` (~line 122) is deliberately
//! different: any future `Participant` field must update both that projection
//! and this module.

use super::*;
use std::collections::HashSet;

/// The eight column names that differ between the ordinary JOIN projection and
/// the participant-scoped find projection.
#[derive(Clone, Copy)]
pub(crate) struct GroupProjectionColumns {
    group_kind: &'static str,
    dm_pair_key: &'static str,
    bot_uuid: &'static str,
    role: &'static str,
    actor_kind: &'static str,
    mode: &'static str,
    tags_json: &'static str,
    message_view_scope: &'static str,
}

/// Ordinary projection used by the single/bulk load and the list queries.
pub(crate) const GROUP_COLUMNS: GroupProjectionColumns = GroupProjectionColumns {
    group_kind: "group_kind",
    dm_pair_key: "dm_pair_key",
    bot_uuid: "bot_uuid",
    role: "role",
    actor_kind: "actor_kind",
    mode: "mode",
    tags_json: "tags_json",
    message_view_scope: "message_view_scope",
};

/// Aliased projection used by the two non-paginated participant find queries.
/// The paginated participant find is the deliberate exception: it uses
/// `GROUP_COLUMNS` because its SQL omits `message_view_scope` (the locked
/// missing-column quirk).
pub(crate) const PARTICIPANT_FIND_ALIASED_COLUMNS: GroupProjectionColumns = GroupProjectionColumns {
    group_kind: "g_group_kind",
    dm_pair_key: "g_dm_pair_key",
    bot_uuid: "p_bot_uuid",
    role: "p_role",
    actor_kind: "p_actor_kind",
    mode: "p_mode",
    tags_json: "p_tags_json",
    message_view_scope: "p_message_view_scope",
};

/// Construct the single canonical `Group` projection.
///
/// Callers pass the fields they decoded with entry-specific strictness (the
/// single-group read rejects a non-string `driver_bot` and an invalid notify
/// mode; the JOIN entries keep their existing tolerant fallbacks) and the
/// participants already loaded for the Group. Every other column is decoded
/// here once for all entry points.
///
/// Only the two group-level column names (`group_kind`/`dm_pair_key`) are read
/// from `columns` here; the member-level names are consumed per row by
/// `insert_join_row`.
pub(crate) fn group_from_row(
    row: &DbRow,
    columns: GroupProjectionColumns,
    id: String,
    driver_bot: String,
    opening_message: Option<OpeningMessage>,
    human_mention_notify_mode: HumanMentionNotifyMode,
    participants: Vec<Participant>,
) -> Group {
    // Tolerance is by contract: strictness lives in the callers' pre-decode.
    let label: Option<String> = db_get_column_opt(row, "label").ok().flatten();
    let status_str: String = db_get_column(row, "status").unwrap_or_default();
    let originator: Option<String> = db_get_column_opt(row, "originator").ok().flatten();
    let routing_policy_json: Option<String> =
        db_get_column_opt(row, "routing_policy_json").ok().flatten();
    let context: Option<String> = db_get_column_opt(row, "context").ok().flatten();
    let service_group_uuid: Option<String> =
        db_get_column_opt(row, "service_group_uuid").ok().flatten();
    let service_mode: Option<String> = db_get_column_opt(row, "service_mode").ok().flatten();
    let version: i32 = db_get_column_opt::<i64>(row, "version")
        .ok()
        .flatten()
        .unwrap_or(1) as i32;
    let record_status: String = db_get_column_opt(row, "record_status")
        .ok()
        .flatten()
        .unwrap_or_else(|| "active".to_string());
    let created_ts: Option<i64> = db_get_column_opt(row, "created_ts").ok().flatten();
    let updated_ts: Option<i64> = db_get_column_opt(row, "updated_ts").ok().flatten();
    let group_kind_str: Option<String> =
        db_get_column_opt(row, columns.group_kind).ok().flatten();
    let dm_pair_key: Option<String> = db_get_column_opt(row, columns.dm_pair_key).ok().flatten();
    let group_strategy_str: Option<String> =
        db_get_column_opt(row, "group_strategy").ok().flatten();
    let group_strategy = MySqlGroupStore::parse_group_strategy(group_strategy_str.as_deref());
    let visibility: String = db_get_column_opt(row, "visibility")
        .ok()
        .flatten()
        .unwrap_or_else(|| "private".to_string());

    Group {
        id,
        label,
        status: MySqlGroupStore::str_to_status(&status_str),
        driver_bot,
        originator,
        routing_policy: MySqlGroupStore::deserialize_routing_policy(routing_policy_json),
        human_mention_notify_mode,
        context,
        opening_message,
        participants,
        messages: Vec::new(),
        service_group_uuid,
        service_mode,
        created_at: MySqlGroupStore::seconds_to_millis(created_ts),
        updated_at: MySqlGroupStore::seconds_to_millis(updated_ts),
        group_kind: MySqlGroupStore::parse_group_kind(group_kind_str.as_deref()),
        dm_pair_key,
        group_strategy,

        version,
        record_status,
        visibility,
    }
}

/// Insert one JOIN row into the batch aggregation map.
///
/// `seen` only decides whether a member is new; encounter order comes from the
/// `participants` vector, never from the set. The first occurrence wins and a
/// duplicate row must not overwrite its role. A Group is constructed only for
/// its first row, and a LEFT JOIN row without a member still yields the Group.
/// Deduplication costs one expected O(1) set insertion per row (O(k) extra
/// space per Group) instead of a linear scan of the participant vector.
pub(crate) fn insert_join_row(
    groups: &mut HashMap<String, (Group, HashSet<String>)>,
    row: &DbRow,
    columns: GroupProjectionColumns,
    env: &str,
    group_id: String,
    opening_message: Option<OpeningMessage>,
) {
    let (group, seen) = groups.entry(group_id.clone()).or_insert_with(|| {
        let group = group_from_row(
            row,
            columns,
            group_id.clone(),
            db_get_column(row, "driver_bot").unwrap_or_default(),
            opening_message,
            MySqlGroupStore::human_mention_notify_mode_from_row(row, &group_id),
            Vec::new(),
        );
        (group, HashSet::new())
    });
    push_participant(group, seen, row, columns, env);
}

fn push_participant(
    group: &mut Group,
    seen: &mut HashSet<String>,
    row: &DbRow,
    columns: GroupProjectionColumns,
    env: &str,
) {
    let (Ok(bot_uuid), Ok(role_str)) = (
        db_get_column::<String>(row, columns.bot_uuid),
        db_get_column::<String>(row, columns.role),
    ) else {
        return;
    };
    if !seen.insert(bot_uuid.clone()) {
        return;
    }
    let actor_kind_str: Option<String> =
        db_get_column_opt(row, columns.actor_kind).ok().flatten();
    let mode_str: Option<String> = db_get_column_opt(row, columns.mode).ok().flatten();
    let (actor_kind, mode) = MySqlGroupStore::normalize_kind_mode(
        &group.id,
        &bot_uuid,
        env,
        actor_kind_str.as_deref(),
        mode_str.as_deref(),
    );
    group.participants.push(Participant {
        bot_uuid: bot_uuid.clone(),
        bot_name: None,
        kind: Some(ParticipantKind::Bot),
        role: MySqlGroupStore::str_to_role(&role_str),
        actor_kind,
        mode: Some(mode),
        tags: MySqlGroupStore::deserialize_participant_tags(
            db_get_column_opt(row, columns.tags_json).ok().flatten(),
        ),
        message_view_scope: MySqlGroupStore::message_view_scope_from_row(
            row,
            columns.message_view_scope,
            &group.id,
            &bot_uuid,
            actor_kind,
        ),
    });
}

/// Collect the aggregate into list order: `updated_at` descending, then ID
/// ascending for deterministic pagination when timestamps tie.
pub(crate) fn into_sorted_groups(
    groups: HashMap<String, (Group, HashSet<String>)>,
) -> Vec<Group> {
    let mut groups: Vec<Group> = groups
        .into_values()
        .map(|(group, _)| group)
        .collect();
    Group::sort_by_updated_at_desc(&mut groups);
    groups
}
