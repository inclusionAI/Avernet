use std::collections::HashMap;

use async_trait::async_trait;

use super::{ActorKind, GroupMessage, ServiceResult};

pub use bcs_domain::{
    DefaultDelivery, Group, GroupKind, GroupStatus, GroupStrategy, Participant, ParticipantKind,
    ParticipantMode, ParticipantRole, RoutingMode, RoutingPolicy, SenderRoutesValidationError,
    ServiceSpec, Workspace,
};

/// Actor input for actor-level DM group creation.
#[derive(Debug, Clone)]
pub struct DmActorSpec {
    pub actor_id: String,
    pub actor_kind: ActorKind,
    pub display_name: Option<String>,
}

pub use crate::types::GroupMutableFieldsPatch;

/// Validate sender_routes against group participants.
///
/// Checks:
/// 1. No self-referencing (sender in own targets)
/// 2. No cycles in the directed graph (DFS)
/// 3. All sender and target bot_ids are current group participants
/// 4. Each sender has at most 10 targets
/// 5. Total entries do not exceed participant count
pub fn validate_sender_routes(
    sender_routes: &HashMap<String, Vec<String>>,
    participant_ids: &[&str],
) -> Result<(), SenderRoutesValidationError> {
    let participants: std::collections::HashSet<&str> = participant_ids.iter().copied().collect();

    // 5. Total entries limit
    if sender_routes.len() > participants.len() {
        return Err(SenderRoutesValidationError::TooManyEntries(
            sender_routes.len(),
            participants.len(),
        ));
    }

    for (sender, targets) in sender_routes {
        // 3. Sender must be a participant
        if !participants.contains(sender.as_str()) {
            return Err(SenderRoutesValidationError::NotAParticipant(sender.clone()));
        }

        // 4. Target count limit
        if targets.len() > 10 {
            return Err(SenderRoutesValidationError::TooManyTargets(
                sender.clone(),
                targets.len(),
            ));
        }

        for target in targets {
            // 1. No self-referencing
            if target == sender {
                return Err(SenderRoutesValidationError::SelfReference(sender.clone()));
            }

            // 3. Target must be a participant
            if !participants.contains(target.as_str()) {
                return Err(SenderRoutesValidationError::NotAParticipant(target.clone()));
            }
        }
    }

    // 2. Cycle detection via DFS
    // Build adjacency list
    let mut visited: HashMap<&str, u8> = HashMap::new(); // 0=unvisited, 1=in_stack, 2=done
    for sender in sender_routes.keys() {
        visited.insert(sender.as_str(), 0);
    }

    fn dfs<'a>(
        node: &'a str,
        routes: &'a HashMap<String, Vec<String>>,
        visited: &mut HashMap<&'a str, u8>,
        path: &mut Vec<&'a str>,
    ) -> Result<(), SenderRoutesValidationError> {
        visited.insert(node, 1); // mark as in-stack
        path.push(node);

        if let Some(targets) = routes.get(node) {
            for target in targets {
                match visited.get(target.as_str()).copied().unwrap_or(0) {
                    1 => {
                        // Found cycle — build descriptive path
                        path.push(target.as_str());
                        let cycle_start = path.iter().position(|&n| n == target.as_str()).unwrap();
                        let cycle: Vec<&str> = path[cycle_start..].to_vec();
                        return Err(SenderRoutesValidationError::CycleDetected(
                            cycle.join(" -> "),
                        ));
                    }
                    0 => {
                        dfs(target.as_str(), routes, visited, path)?;
                    }
                    _ => {} // already fully processed
                }
            }
        }

        path.pop();
        visited.insert(node, 2); // mark as done
        Ok(())
    }

    for sender in sender_routes.keys() {
        if visited.get(sender.as_str()).copied().unwrap_or(0) == 0 {
            let mut path = Vec::new();
            dfs(sender.as_str(), sender_routes, &mut visited, &mut path)?;
        }
    }

    Ok(())
}

/// Service for group management.
#[async_trait]
pub trait GroupCoreService: Send + Sync {
    /// Create or update a group.
    async fn upsert(&self, group: Group) -> ServiceResult<()>;

    /// Atomically patch only the mutable OpenAPI v1 fields that are present.
    async fn patch_mutable_fields(
        &self,
        id: &str,
        patch: GroupMutableFieldsPatch,
    ) -> ServiceResult<()> {
        let _ = (id, patch);
        Err(super::ServiceError::InvalidOperation {
            message: "atomic mutable Group patch is not configured".to_string(),
            request_id: None,
        })
    }

    /// Get a group by ID.
    async fn get(&self, id: &str) -> Option<Group>;

    /// Add a message to a group.
    async fn add_message(&self, id: &str, message: GroupMessage) -> ServiceResult<()>;

    /// Add a participant to a group.
    async fn add_participant(&self, id: &str, participant: Participant) -> ServiceResult<()>;

    /// Remove a participant from a group by bot_uuid.
    async fn remove_participant(&self, group_id: &str, bot_uuid: &str) -> ServiceResult<()>;

    /// Update an existing participant's `mode` (Human Actor V1, Task P.1).
    ///
    /// Idempotent: if the new `mode` equals the current one, returns Ok without
    /// any DB write. Returns `GroupNotFound` if the group is missing and
    /// `ParticipantNotFound` if the actor is not in the group.
    ///
    /// Caller is responsible for validating that `mode.is_valid_for(actor_kind)`
    /// before calling this method (handler-level concern).
    async fn update_participant_mode(
        &self,
        group_id: &str,
        actor_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()>;

    /// Insert a Human participant into an existing group (Human Actor V1, Task P.1).
    ///
    /// Convenience wrapper that constructs a `Participant` with
    /// `actor_kind=Human`, `role=Observer`, and the supplied `mode`,
    /// then delegates to `add_participant`. Idempotent (no-op if the
    /// `(group_id, human_id)` row already exists).
    async fn insert_human_participant(
        &self,
        group_id: &str,
        human_id: &str,
        mode: ParticipantMode,
    ) -> ServiceResult<()> {
        let participant = Participant {
            bot_uuid: human_id.to_string(),
            bot_name: None,
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: Some(mode),
        };
        self.add_participant(group_id, participant).await
    }

    /// Update group workspace.
    async fn update_workspace(&self, id: &str, workspace: Workspace) -> ServiceResult<()>;

    /// Update group label.
    async fn update_label(&self, id: &str, label: Option<String>) -> ServiceResult<()>;

    /// Update group status.
    async fn update_status(&self, id: &str, status: GroupStatus) -> ServiceResult<()>;

    /// Replace (or remove) the group's `service_spec`. Validation of the
    /// patch (route-field lock, callback_config immutability) is done by the
    /// HTTP/use-case layer; this contract just persists the new value.
    async fn update_service_spec(
        &self,
        id: &str,
        service_spec: Option<ServiceSpec>,
    ) -> ServiceResult<()>;

    /// Terminate a group.
    ///
    /// Only the driver/coordinator can terminate a group.
    /// Sets status to `Completed` and returns the final group state.
    ///
    /// # Arguments
    /// * `id` - The group ID
    /// * `caller_bot_id` - The bot_id of the caller (must be the driver)
    ///
    /// # Returns
    /// The final group state, or error if not authorized or not found.
    async fn terminate(&self, id: &str, caller_bot_id: &str) -> ServiceResult<Group>;

    /// Delete a group.
    async fn delete(&self, id: &str) -> ServiceResult<Option<Group>>;

    /// List all sessions.
    async fn list(&self) -> Vec<Group>;

    /// List groups with pagination, ordered by `updated_at` descending.
    /// - MySQL: uses SQL LIMIT/OFFSET after ordering
    /// - Memory: orders then slices the result after loading
    async fn list_paginated(&self, offset: u64, limit: u64) -> Vec<Group>;

    /// Find all sessions where the given bot is a participant.
    ///
    /// Return order is intentionally undefined; externally visible callers
    /// must apply their own ordering before pagination or response mapping.
    async fn find_by_participant(&self, bot_uuid: &str) -> Vec<Group>;

    /// Find groups by participant, optionally filtered by group kind and label
    /// query. `label_query` matches label only; group id is intentionally not
    /// part of the search semantics.
    async fn find_by_participant_filtered(
        &self,
        bot_uuid: &str,
        kind: Option<GroupKind>,
        label_query: Option<&str>,
    ) -> Vec<Group> {
        let label_query = label_query.map(str::trim).filter(|q| !q.is_empty());
        self.find_by_participant(bot_uuid)
            .await
            .into_iter()
            .filter(|group| kind.is_none_or(|kind| group.group_kind == kind))
            .filter(|group| {
                label_query.is_none_or(|q| {
                    group
                        .label
                        .as_deref()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(&q.to_lowercase())
                })
            })
            .collect()
    }

    /// Count all groups.
    async fn count(&self) -> u64;

    /// Count groups where the given bot is a participant.
    async fn count_by_participant(&self, bot_uuid: &str) -> u64;

    /// Find groups by participant with pagination, ordered by `updated_at`
    /// descending before `offset` / `limit` are applied.
    async fn find_by_participant_paginated(
        &self,
        bot_uuid: &str,
        offset: u64,
        limit: u64,
    ) -> Vec<Group>;

    /// Get the current message count for a group.
    async fn message_count(&self, id: &str) -> ServiceResult<usize>;

    /// Increment the message counter for a group (independent of add_message).
    async fn increment_message_count(&self, id: &str) -> ServiceResult<()>;

    /// Reset the message counter for a group to zero.
    async fn reset_message_count(&self, id: &str) -> ServiceResult<()>;

    /// CR-4: count groups optionally filtered by `group_kind`.
    ///
    /// `kind=None` → count all groups (equivalent to legacy `count()`).
    /// `kind=Some(GroupKind::Dm)` → count only dm groups.
    /// `kind=Some(GroupKind::Normal)` → count only normal groups.
    ///
    /// Implementations own filtering semantics. The contract default is an
    /// empty stub for lightweight mocks only; production stores must override.
    async fn count_by_kind(&self, kind: Option<GroupKind>) -> u64 {
        let _ = kind;
        0
    }

    /// CR-4: paginate groups optionally filtered by `group_kind`.
    ///
    /// Same semantics as `list_paginated` but the `offset` / `limit`
    /// window is applied **after** filtering by `kind` and ordering by
    /// `updated_at` descending. This means pagination is stable per-filter:
    /// a client paging through `kind=dm` only sees dm groups and `total`
    /// (from `count_by_kind(Some(Dm))`) matches the result set.
    ///
    /// Implementations own filtering and pagination semantics. The contract
    /// default is an empty stub for lightweight mocks only; production stores
    /// must override.
    async fn list_paginated_by_kind(
        &self,
        kind: Option<GroupKind>,
        offset: u64,
        limit: u64,
    ) -> Vec<Group> {
        let _ = (kind, offset, limit);
        Vec::new()
    }

    /// Update group visibility ("public" or "private").
    ///
    /// Implementations own persistence semantics. The contract default is an
    /// empty stub for lightweight mocks only; production stores must override.
    async fn update_visibility(&self, id: &str, visibility: &str) -> ServiceResult<()> {
        let _ = (id, visibility);
        Ok(())
    }

    /// Count groups matching the given filters (kind, visibility, label).
    ///
    /// Implementations own filtering semantics. The contract default is an
    /// empty stub for lightweight mocks only; production stores must override.
    async fn count_filtered(
        &self,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> u64 {
        let _ = (kind, visibility, label);
        0
    }

    /// List groups filtered by kind, visibility, and label substring.
    /// Results are ordered by `updated_at` descending with pagination.
    ///
    /// Implementations own filtering and pagination semantics. The contract
    /// default is an empty stub for lightweight mocks only; production stores
    /// must override.
    async fn list_paginated_filtered(
        &self,
        offset: u64,
        limit: u64,
        kind: Option<GroupKind>,
        visibility: Option<&str>,
        label: Option<&str>,
    ) -> Vec<Group> {
        let _ = (offset, limit, kind, visibility, label);
        Vec::new()
    }

    /// Find an existing `Dm` group by its canonical pair key (Task G.2).
    ///
    /// Returns the group if a row with `group_kind='dm' AND dm_pair_key=key`
    /// exists for the current environment, otherwise `None`. Backed by the
    /// `(env, dm_pair_key)` unique index on `bcs_groups` (migration 005).
    ///
    /// Implementations own lookup semantics. The contract default is an
    /// empty stub for lightweight mocks only; production stores must override.
    async fn find_dm_by_pair_key(&self, dm_pair_key: &str) -> Option<Group> {
        let _ = dm_pair_key;
        None
    }

    /// Create-or-reuse an actor-level `Dm` group atomically.
    ///
    /// Supports Bot↔Bot and Human↔Bot pairs. Human↔Human and identical actor
    /// pairs are invalid. Implementations must preserve canonical pair identity
    /// on reuse and must not mutate existing DM identity fields.
    async fn create_or_reuse_actor_dm_group(
        &self,
        id: &str,
        actor_a: DmActorSpec,
        actor_b: DmActorSpec,
        legacy_driver_bot: &str,
        originator_actor_id: &str,
        label: Option<String>,
        context: Option<String>,
    ) -> ServiceResult<(Group, bool)>;

    /// Create-or-reuse a `Dm` group atomically (Task G.2 + CR-1 fix).
    ///
    /// Returns `(group, created)` where `created=true` ⇔ this call inserted
    /// the row and `created=false` ⇔ the row already existed (concurrent
    /// caller, reverse-direction reuse, or retry).
    ///
    /// **Why this contract changed (CR-1)**:
    /// the previous version of this method blindly did `upsert(...)`, which
    /// on the MySQL backend translates to `INSERT ... ON DUPLICATE KEY
    /// UPDATE` against the `(env, dm_pair_key)` unique index. On conflict
    /// that overwrote the *existing* group's `driver_bot` / `label` /
    /// `participants` and returned the *locally-constructed* `Group` whose
    /// `id` was never inserted. The handler then surfaced a `group_id` that
    /// didn't exist in the DB. The new contract:
    /// 1. `find_dm_by_pair_key` first; if hit → `(existing, false)` and the
    ///    caller's `id` / `label` / `driver_bot` are silently dropped (this
    ///    is the documented reverse-reuse semantics — Requirement 3.19,
    ///    Task G.3 acceptance "reverse reuse driver_bot 不变").
    /// 2. Otherwise insert; on race the second caller's insert fails on the
    ///    unique index, we re-read by `pair_key` and return `(existing, false)`.
    /// 3. Identity columns (`group_kind`, `dm_pair_key`, `driver_bot`,
    ///    `label`, `participants`) MUST NOT be mutated by step 2's insert
    ///    when the row already exists.
    ///
    /// Production stores must implement this in the concrete service crate.
    async fn create_or_reuse_dm_group(
        &self,
        id: &str,
        driver_bot: &str,
        bot_a: &str,
        bot_b: &str,
        label: Option<String>,
    ) -> ServiceResult<(Group, bool)> {
        self.create_or_reuse_actor_dm_group(
            id,
            DmActorSpec {
                actor_id: bot_a.to_string(),
                actor_kind: ActorKind::Bot,
                display_name: None,
            },
            DmActorSpec {
                actor_id: bot_b.to_string(),
                actor_kind: ActorKind::Bot,
                display_name: None,
            },
            driver_bot,
            driver_bot,
            label,
            None,
        )
        .await
    }
}
