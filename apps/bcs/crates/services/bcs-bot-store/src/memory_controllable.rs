//! `list_controllable` — the mine-union candidate read on
//! the SAME `MemoryBotRepo` state (bot-authority plan
//! Task 4.5, spec §8.0 point 1).
//!
//! ONE snapshot of the approved `owner|manager` edge
//! union of `human_<user_id>` in the queried env, joined
//! to live Bot rows filtered by the persistent
//! conditions (env/kind/name/status), role-deduped
//! (owner wins), stably sorted by bot id and INCLUDING
//! the legal Human self entry (Owner DISPLAY tag — no
//! Human→Human owner edge exists). The SQL store's
//! `PersistentBotRepo::list_controllable` (in
//! `controllable.rs`) mirrors this exactly; the shared
//! conformance suite pins the identical semantics.

use std::collections::HashSet;

use bcs_service_api::types::bot_authority::{
    BotAccessRelation, human_actor_id,
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotControlPlaneRecord, BotControlPlaneRepoPort,
    ControllableCandidateQuery, ControlledBotCandidate,
    ServiceResult,
};

use super::authority::{MemoryAuthorityEdgeKind, MemoryAuthorityEdgeStatus};
use super::MemoryBotRepo;

/// Approved owner/manager edges of the Human actor, one
/// relation per bot, owner preferred (spec §8 mine: 并集、
/// 去重，逐项 access_relation=owner|manager).
fn edge_relations(
    edges: impl Iterator<Item = (String, MemoryAuthorityEdgeKind)>,
) -> Vec<(String, BotAccessRelation)> {
    let mut owners = HashSet::new();
    let mut managers = HashSet::new();
    for (bot_id, kind) in edges {
        match kind {
            MemoryAuthorityEdgeKind::Owner => {
                owners.insert(bot_id);
            }
            MemoryAuthorityEdgeKind::Manager => {
                managers.insert(bot_id);
            }
            // Runtime kinds never satisfy authority reads
            // (A16 direction).
            _ => {}
        }
    }
    // Role dedup: manager rows never overwrite an
    // owner.
    managers.retain(|bot_id| !owners.contains(bot_id));
    owners
        .into_iter()
        .map(|bot_id| (bot_id, BotAccessRelation::Owner))
        .chain(
            managers
                .into_iter()
                .map(|bot_id| (bot_id, BotAccessRelation::Manager)),
        )
        .collect()
}

/// The persistent filters shared with the SQL store.
/// A `None` field does not constrain.
fn persistent_filters_met(
    record: &BotControlPlaneRecord,
    kind: Option<ActorKind>,
    status: Option<ActorStatus>,
    name_filter: Option<&str>,
) -> bool {
    kind.is_none_or(|wanted| record.kind == wanted)
        && status.is_none_or(|wanted| record.status == wanted)
        && name_filter
            .is_none_or(|needle| record.name.to_lowercase().contains(&needle))
}

fn candidate_of(
    record: &BotControlPlaneRecord,
    relation: BotAccessRelation,
) -> ControlledBotCandidate {
    ControlledBotCandidate {
        id: record.bot_id.clone(),
        kind: record.kind,
        status: record.status,
        access_relation: relation,
    }
}

/// The one-snapshot read; called from the
/// `BotControlPlaneRepoPort` implementation in
/// `memory_control_plane.rs`.
pub(super) async fn memory_repo_candidates(
    repo: &MemoryBotRepo,
    query: &ControllableCandidateQuery,
) -> ServiceResult<Vec<ControlledBotCandidate>> {
    // Edge snapshots use the composed Human actor id;
    // the query carries the raw User ID.
    let owner_actor = human_actor_id(&query.user_id);
    let name_filter = query
        .name
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_lowercase);

    // ONE snapshot of the approved edge union; the
    // guard is released before the per-row record
    // reads (no lock is held across awaits).
    let relations: Vec<(String, BotAccessRelation)> = {
        let authority = repo.authority.read().await;
        edge_relations(
            authority.edges.iter().filter_map(
                |(key, (status, ref_id))| {
                    if key.0 != query.env || key.2 != owner_actor {
                        return None;
                    }
                    if *status != MemoryAuthorityEdgeStatus::Approved
                        || *ref_id != 0
                    {
                        return None;
                    }
                    Some((key.1.clone(), key.3))
                },
            ),
        )
    };

    let ids: Vec<String> = repo
        .bots
        .read()
        .await
        .keys()
        .cloned()
        .collect();
    let mut candidates = Vec::new();
    let mut seen = HashSet::new();
    for bot_id in ids {
        // Deleted bots authorize nobody and never
        // re-enter the list.
        if repo.deleted_bot_ids.read().await.contains(&bot_id) {
            continue;
        }
        let Some(record) =
            repo.get_control_plane(&bot_id, &query.env).await?
        else {
            continue;
        };
        let edge_relation = relations
            .iter()
            .find(|(edge_bot_id, _)| edge_bot_id == &record.bot_id)
            .map(|(_, relation)| *relation);
        // Edge-backed candidates carry their authority
        // relation; the ONLY edge-less candidate is the
        // legal Human self entry (spec §8.0 point 3):
        // Owner display tag, no Human→Human owner edge
        // exists or is created.
        let relation = edge_relation.or_else(|| {
            (record.kind == ActorKind::Human
                && record.bot_id == owner_actor)
                .then_some(BotAccessRelation::Owner)
        });
        let relation = match relation {
            Some(relation) => relation,
            None => {
                // Migration-window compat arm (spec §10.2a):
                // live physical Bot, byte-exact stored creator,
                // ownership still uninitialized — the legacy
                // fallback shim would read Owner for the caller,
                // so this page surfaces the Bot until the lazy
                // sweep mints its edge.
                let version = repo
                    .authority
                    .read()
                    .await
                    .ownership_versions
                    .get(&(query.env.clone(), record.bot_id.clone()))
                    .copied()
                    .unwrap_or(0);
                if query.include_legacy_creator
                    && record.kind == ActorKind::Bot
                    && record.created_by.as_deref() == Some(query.user_id.as_str())
                    && version == 0
                {
                    BotAccessRelation::Owner
                } else {
                    continue;
                }
            }
        };
        if !persistent_filters_met(
            &record,
            query.kind,
            query.status,
            name_filter.as_deref(),
        ) {
            continue;
        }
        if seen.insert(record.bot_id.clone()) {
            candidates.push(candidate_of(&record, relation));
        }
    }
    // Stable order: bot id ascending.
    candidates.sort_by(|left, right| left.id.cmp(&right.id));
    Ok(candidates)
}
