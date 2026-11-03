//! `list_controllable` — the mine-union candidate read
//! (bot-authority plan Task 4.5, spec §8.0 point 1).
//!
//! SQL companions of the `BotControlPlaneRepoPort`
//! `list_controllable` implementation in
//! `persistent_repo::control_plane`; the memory
//! projection mirrors them in `memory_controllable.rs`
//! and the shared conformance suite pins the identical
//! semantics for both stores.
//!
//! ONE store snapshot returns the lightweight
//! controllable candidates of a Human: the approved
//! `owner|manager` edge union of `human_<user_id>` in
//! the queried env, joined to live Bot rows filtered
//! by the persistent conditions (env/kind/name/
//! status), role-deduped (owner wins over manager),
//! stably sorted by bot id and INCLUDING the legal
//! Human self entry. Only identity columns (id/kind/
//! status plus the edge relation) leave the store —
//! no full Bot configuration is read to count.
//!
//! There is deliberately NO pagination and NO
//! reachability here: those are the application's §8.0
//! dynamic-algorithm concerns (one
//! `try_runtime_activity_for` observation, then
//! filter → total → page → backfill). Storage errors
//! propagate — a failing store is never a silently
//! empty mine list.

use std::collections::HashSet;

use bcs_db_api::{DbRow, DbValue as Value, db_get_column_opt};
use bcs_service_api::types::bot_authority::{
    BotAccessRelation, human_actor_id,
};
use bcs_service_api::{
    ActorKind, ActorStatus, ControllableCandidateQuery,
    ControlledBotCandidate, ServiceError, ServiceResult,
};

use super::PersistentBotRepo;

fn store_error(error: impl std::fmt::Display) -> ServiceError {
    ServiceError::InternalError(error.to_string())
}

fn relation_of(kind: &str) -> BotAccessRelation {
    if kind == "owner" {
        BotAccessRelation::Owner
    } else {
        BotAccessRelation::Manager
    }
}

fn kind_of(raw: &str) -> ActorKind {
    if raw == "human" {
        ActorKind::Human
    } else {
        ActorKind::Bot
    }
}

fn status_of(raw: &str) -> ActorStatus {
    if raw == "hidden" {
        ActorStatus::Hidden
    } else {
        ActorStatus::Online
    }
}

fn row_candidate(row: &DbRow) -> ControlledBotCandidate {
    let cell = |column: &str| {
        db_get_column_opt::<String>(row, column)
            .ok()
            .flatten()
            .unwrap_or_default()
    };
    let id = cell("bot_uuid");
    let kind = kind_of(&cell("actor_kind"));
    let status = status_of(&cell("status"));
    let access = cell("access_kind");
    ControlledBotCandidate {
        id,
        kind,
        status,
        access_relation: relation_of(&access),
    }
}

/// Role dedup over the SAME bot in the returned order:
/// owner rows come before manager rows, so a bot seen as
/// owner first keeps the Owner relation (spec §8 mine:
/// 并集、去重，逐项 access_relation=owner|manager).
fn dedup_candidates(
    rows: Vec<ControlledBotCandidate>,
) -> Vec<ControlledBotCandidate> {
    let mut seen_owner = HashSet::new();
    let mut seen_manager = HashSet::new();
    let mut candidates = Vec::new();
    for candidate in rows {
        let already = match candidate.access_relation {
            BotAccessRelation::Owner => {
                !seen_owner.insert(candidate.id.clone())
            }
            BotAccessRelation::Manager => {
                seen_owner.contains(&candidate.id)
                    || !seen_manager.insert(candidate.id.clone())
            }
        };
        if !already {
            candidates.push(candidate);
        }
    }
    candidates
}

/// The one-snapshot SQL read; called from the
/// `BotControlPlaneRepoPort` implementation in
/// `persistent_repo::control_plane`.
pub(super) async fn sql_list_controllable(
    repo: &PersistentBotRepo,
    query: &ControllableCandidateQuery,
) -> ServiceResult<Vec<ControlledBotCandidate>> {
    // Edges store the Human ACTOR id; the query carries
    // the raw User ID (created_by convention of this
    // layer). Byte-exact in both dialects.
    let owner_actor = human_actor_id(&query.user_id);
    let from_predicate = match repo.flavor {
        bcs_db_api::DbSqlFlavor::Mysql => "CAST(e.from_id AS BINARY) = ?",
        bcs_db_api::DbSqlFlavor::Sqlite => "e.from_id = ?",
    };
    let name_filter = query
        .name
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());

    // Persistent filters inside each UNION arm share
    // one shape; a `None` field does not constrain.
    let filters_for =
        |alias: &str, params: &mut Vec<Value>| -> String {
            let mut sql = String::new();
            if let Some(kind) = query.kind {
                sql.push_str(&format!(
                    " AND COALESCE({alias}.actor_kind, 'bot') = ?"
                ));
                params.push(Value::from(match kind {
                    ActorKind::Bot => "bot",
                    ActorKind::Human => "human",
                }));
            }
            if let Some(status) = query.status {
                sql.push_str(&format!(" AND {alias}.status = ?"));
                params.push(Value::from(match status {
                    ActorStatus::Online => "online",
                    ActorStatus::Hidden => "hidden",
                }));
            }
            if let Some(name) = name_filter {
                sql.push_str(&format!(
                    " AND INSTR(LOWER({alias}.name), ?) > 0"
                ));
                params.push(Value::from(name.to_lowercase()));
            }
            sql
        };

    // Both UNION arms share ONE env and ONE set of
    // persistent filters: (a) the approved edge union
    // joined to LIVE bot rows, (b) the Human self row
    // (Owner display tag; no Human→Human owner edge
    // exists or is created). `edge_rank` puts owner
    // rows before manager rows so the shared dedup
    // keeps the Owner relation.
    let mut params: Vec<Value> = Vec::new();
    params.push(Value::from(query.env.as_str()));
    params.push(Value::from(owner_actor.as_str()));
    let edge_filters = filters_for("b", &mut params);
    params.push(Value::from(query.env.as_str()));
    params.push(Value::from(owner_actor.as_str()));
    let self_filters = filters_for("s", &mut params);
    // Migration-window compat arm (spec §10.2a): while
    // `include_legacy_creator` is on, live physical Bots whose
    // stored creator byte-matches the caller and whose ownership
    // is still uninitialized (version 0) surf on the same page —
    // they already read through the legacy fallback shim, and the
    // lazy sweep has not minted them edges yet. Project them as
    // `owner` (the fallback's own role read returns Owner); once
    // initialized the edge arm carries them and this arm stops
    // matching.
    let legacy_arm = if query.include_legacy_creator {
        params.push(Value::from(query.env.as_str()));
        params.push(Value::from(query.user_id.as_str()));
        params.push(Value::from(query.user_id.as_str()));
        "UNION ALL \
         SELECT l.bot_uuid, \
                COALESCE(l.actor_kind, 'bot') AS actor_kind, \
                l.status, \
                'owner' AS access_kind \
         FROM bcs_bots l \
         WHERE l.env = ? \
           AND COALESCE(l.is_deleted, 0) = 0 \
           AND COALESCE(l.actor_kind, 'bot') = 'bot' \
           AND l.ownership_version = 0 \
           AND l.created_by = ? AND HEX(l.created_by) = HEX(?) \
           {legacy_filters}"
            .replace("{legacy_filters}", &filters_for("l", &mut params))
    } else {
        String::new()
    };

    let sql = format!(
        "SELECT t.bot_uuid, t.actor_kind, t.status, \
                t.access_kind \
         FROM (\
           SELECT b.bot_uuid, \
                  COALESCE(b.actor_kind, 'bot') AS actor_kind, \
                  b.status, \
                  CASE WHEN e.grant_kind = 'owner' \
                       THEN 'owner' ELSE 'manager' END AS access_kind \
           FROM edge_grants e \
           JOIN bcs_bots b \
             ON b.bot_uuid = e.to_id AND b.env = e.env \
             AND COALESCE(b.is_deleted, 0) = 0 \
           WHERE e.env = ? AND {from_predicate} \
             AND e.grant_kind IN ('owner', 'manager') \
             AND e.grant_ref_id = 0 \
             AND e.rules IS NULL \
             AND e.status = 'approved'\
           {edge_filters} \
           UNION ALL \
           SELECT s.bot_uuid, \
                  COALESCE(s.actor_kind, 'human') AS actor_kind, \
                  s.status, \
                  'owner' AS access_kind \
           FROM bcs_bots s \
           WHERE s.env = ? \
             AND COALESCE(s.is_deleted, 0) = 0 \
             AND COALESCE(s.actor_kind, 'bot') = 'human' \
             AND s.bot_uuid = ?\
           {self_filters} \
           {legacy_arm} \
         ) t \
         ORDER BY t.bot_uuid ASC, \
                  CASE WHEN t.access_kind = 'owner' THEN 0 ELSE 1 END ASC"
    );

    let rows = repo
        .db_query(&sql, params)
        .await
        .map_err(store_error)?;
    Ok(dedup_candidates(
        rows.iter().map(row_candidate).collect::<Vec<_>>(),
    ))
}
