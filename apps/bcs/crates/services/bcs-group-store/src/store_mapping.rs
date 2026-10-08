//! Row decoding and enum conversion helpers shared by the Group SQL store.

use super::*;

impl MySqlGroupStore {

    /// Convert GroupStatus to string.
    pub(crate) fn status_to_str(status: &GroupStatus) -> &'static str {
        match status {
            GroupStatus::Active => "active",
            GroupStatus::Completed => "completed",
            GroupStatus::Error => "error",
            GroupStatus::Closed => "closed",
            GroupStatus::Inactive => "inactive",
        }
    }

    /// Convert string to GroupStatus.
    pub(crate) fn str_to_status(s: &str) -> GroupStatus {
        match s {
            "active" => GroupStatus::Active,
            "completed" => GroupStatus::Completed,
            "error" => GroupStatus::Error,
            "closed" => GroupStatus::Closed,
            "inactive" => GroupStatus::Inactive,
            _ => GroupStatus::Active,
        }
    }

    /// Convert ParticipantRole to string.
    pub(crate) fn role_to_str(role: &ParticipantRole) -> &'static str {
        match role {
            ParticipantRole::Driver => "driver",
            ParticipantRole::Consultant => "consultant",
            ParticipantRole::Manager => "manager",
            ParticipantRole::Worker => "worker",
            ParticipantRole::Observer => "observer",
        }
    }

    /// Convert string to ParticipantRole.
    pub(crate) fn str_to_role(s: &str) -> ParticipantRole {
        match s {
            "driver" => ParticipantRole::Driver,
            "consultant" => ParticipantRole::Consultant,
            "manager" => ParticipantRole::Manager,
            "worker" => ParticipantRole::Worker,
            "observer" => ParticipantRole::Observer,
            _ => ParticipantRole::Driver,
        }
    }

    /// Convert UNIX_TIMESTAMP seconds (from MySQL) to milliseconds.
    pub(crate) fn seconds_to_millis(secs: Option<i64>) -> u64 {
        secs.unwrap_or(0) as u64 * 1000
    }

    /// Convert ActorKind to canonical string used in MySQL.
    pub(crate) fn actor_kind_to_str(kind: ActorKind) -> &'static str {
        match kind {
            ActorKind::Bot => "bot",
            ActorKind::Human => "human",
        }
    }

    /// Convert ParticipantMode to canonical string used in MySQL.
    pub(crate) fn mode_to_str(mode: ParticipantMode) -> &'static str {
        match mode {
            ParticipantMode::Auto => "auto",
            ParticipantMode::Muted => "muted",
            ParticipantMode::Present => "present",
            ParticipantMode::Absent => "absent",
        }
    }

    pub(crate) fn message_view_scope_to_str(scope: MessageViewScope) -> &'static str {
        match scope {
            MessageViewScope::Full => "full",
            MessageViewScope::Participant => "participant",
        }
    }

    pub(crate) fn parse_message_view_scope(raw: Option<&str>) -> ServiceResult<MessageViewScope> {
        match raw {
            None | Some("") | Some("full") => Ok(MessageViewScope::Full),
            Some("participant") => Ok(MessageViewScope::Participant),
            Some(other) => Err(ServiceError::InternalError(format!(
                "unknown participant message_view_scope '{other}'"
            ))),
        }
    }

    pub(crate) fn message_view_scope_from_row(
        row: &DbRow,
        column: &str,
        group_id: &str,
        actor_id: &str,
        actor_kind: ActorKind,
    ) -> MessageViewScope {
        let least_privileged = match actor_kind {
            ActorKind::Human => MessageViewScope::Participant,
            ActorKind::Bot => MessageViewScope::Full,
        };
        let raw = match db_get_column_opt::<String>(row, column) {
            Ok(raw) => raw,
            Err(error) => {
                error!(%group_id, %actor_id, %error, "failed to decode participant message view scope");
                return least_privileged;
            }
        };
        match Self::parse_message_view_scope(raw.as_deref()) {
            Ok(scope) if scope.is_valid_for(actor_kind) => scope,
            Ok(scope) => {
                error!(%group_id, %actor_id, ?scope, ?actor_kind, "invalid participant message view scope for actor kind");
                least_privileged
            }
            Err(error) => {
                error!(%group_id, %actor_id, %error, "invalid participant message view scope");
                least_privileged
            }
        }
    }

    /// Convert `GroupKind` to the canonical string stored in
    /// `bcs_groups.group_kind` (Task G.2 / migration 005).
    pub(crate) fn group_kind_to_str(kind: bcs_service_api::GroupKind) -> &'static str {
        match kind {
            bcs_service_api::GroupKind::Normal => "normal",
            bcs_service_api::GroupKind::Dm => "dm",
        }
    }

    /// Parse `bcs_groups.group_kind` column. NULL / unknown values fall back
    /// to `Normal` for backward compatibility with rows that pre-date
    /// migration 005.
    pub(crate) fn parse_group_kind(s: Option<&str>) -> bcs_service_api::GroupKind {
        match s {
            Some("dm") => bcs_service_api::GroupKind::Dm,
            _ => bcs_service_api::GroupKind::Normal,
        }
    }

    /// Convert `GroupStrategy` to the canonical string stored in
    /// `bcs_groups.group_strategy`.
    pub(crate) fn group_strategy_to_str(strategy: GroupStrategy) -> &'static str {
        match strategy {
            GroupStrategy::Chat => "chat",
            GroupStrategy::ManagerWorker => "manager_worker",
            GroupStrategy::StateMachine => "state_machine",
        }
    }

    /// Parse `bcs_groups.group_strategy` column. NULL / unknown values fall back
    /// to `Chat` for backward compatibility with rows that pre-date this column.
    pub(crate) fn parse_group_strategy(s: Option<&str>) -> GroupStrategy {
        match s {
            Some("manager_worker") => GroupStrategy::ManagerWorker,
            Some("state_machine") => GroupStrategy::StateMachine,
            _ => GroupStrategy::Chat,
        }
    }

    /// Parse `actor_kind` column. Unknown / NULL values fall back to `Bot`
    /// (consistent with the DB-level DEFAULT and Requirement 3.16). The
    /// caller is responsible for emitting an `error!` log when this happens
    /// during the normalization step.
    #[allow(dead_code)]
    pub(crate) fn parse_actor_kind(s: Option<&str>) -> ActorKind {
        match s {
            Some("human") => ActorKind::Human,
            _ => ActorKind::Bot,
        }
    }

    /// Parse `mode` column without validating against `actor_kind`. Returns
    /// `None` for unknown values so the normalization step can detect them
    /// and apply `ParticipantMode::default_for(actor_kind)`.
    pub(crate) fn parse_participant_mode_opt(s: Option<&str>) -> Option<ParticipantMode> {
        match s {
            Some("auto") => Some(ParticipantMode::Auto),
            Some("muted") => Some(ParticipantMode::Muted),
            Some("present") => Some(ParticipantMode::Present),
            Some("absent") => Some(ParticipantMode::Absent),
            _ => None,
        }
    }

    /// Normalize an `(actor_kind_str, mode_str)` row pair read from
    /// `bcs_group_participants` into a valid `(ActorKind, ParticipantMode)`
    /// pair, in-memory only (Task M.6, Requirement 3.10#2 and 3.18#6).
    ///
    /// Behavior matrix:
    ///
    /// | actor_kind_str         | mode_str                         | result                                | log    |
    /// |------------------------|----------------------------------|---------------------------------------|--------|
    /// | NULL                   | (any)                            | actor_kind = Bot (compat path)        | none   |
    /// | "bot" / "human"        | NULL                             | mode = default_for(kind)              | none   |
    /// | "bot" / "human"        | valid + matches kind             | mode = parsed                         | none   |
    /// | "bot" / "human"        | valid but illegal for this kind  | mode = default_for(kind), normalized  | ERROR  |
    /// | "bot" / "human"        | unknown string ("supervised", …) | mode = default_for(kind), normalized  | ERROR  |
    /// | unknown string         | (any)                            | actor_kind = Bot, normalized          | ERROR  |
    ///
    /// NULL on either column is a normal compatibility path (existing rows
    /// pre-dating this migration) and MUST NOT spam ERROR logs. Only truly
    /// invalid data — illegal combinations, unrecognized strings — is
    /// surfaced as ERROR with the full set of triage fields:
    /// `group_id, actor_id, actor_kind, mode, env`.
    ///
    /// The offending DB row is NEVER rewritten; it is fixed in-memory so that
    /// downstream business logic always observes a valid combination.
    pub(crate) fn normalize_kind_mode(
        group_id: &str,
        actor_id: &str,
        env: &str,
        actor_kind_str: Option<&str>,
        mode_str: Option<&str>,
    ) -> (ActorKind, ParticipantMode) {
        let kind = match actor_kind_str {
            Some("bot") => ActorKind::Bot,
            Some("human") => ActorKind::Human,
            None => {
                // Compat path: column NULL / absent. Silently default per M.6 (a).
                ActorKind::Bot
            }
            Some(other) => {
                error!(
                    group_id = %group_id,
                    actor_id = %actor_id,
                    env = %env,
                    actor_kind = %other,
                    mode = ?mode_str,
                    "mysql_store: unknown actor_kind value loaded from DB; \
                     normalizing to 'bot' in-memory only"
                );
                ActorKind::Bot
            }
        };

        let mode = match mode_str {
            // (a) NULL / absent — silent compat path, derive default for kind.
            None => ParticipantMode::default_for(kind),
            Some(raw) => {
                match Self::parse_participant_mode_opt(Some(raw)) {
                    Some(m) if m.is_valid_for(kind) => m,
                    Some(m) => {
                        // (b) Recognized mode value but illegal for this kind.
                        let fallback = ParticipantMode::default_for(kind);
                        error!(
                            group_id = %group_id,
                            actor_id = %actor_id,
                            env = %env,
                            actor_kind = ?kind,
                            mode = ?m,
                            "mysql_store: invalid (actor_kind, mode) combination loaded from DB; \
                             normalizing in-memory only"
                        );
                        fallback
                    }
                    None => {
                        // (c) Unrecognized mode string (e.g. "supervised").
                        let fallback = ParticipantMode::default_for(kind);
                        error!(
                            group_id = %group_id,
                            actor_id = %actor_id,
                            env = %env,
                            actor_kind = ?kind,
                            mode = %raw,
                            "mysql_store: unrecognized mode value loaded from DB; \
                             normalizing in-memory only"
                        );
                        fallback
                    }
                }
            }
        };

        (kind, mode)
    }

    // ========== MySQL Operations ==========

    /// Deserialize routing_policy_json column into Option<RoutingPolicy>.
    pub(crate) fn deserialize_routing_policy(json_str: Option<String>) -> Option<RoutingPolicy> {
        json_str.and_then(|s| {
            if s.is_empty() {
                return None;
            }
            match serde_json::from_str::<RoutingPolicy>(&s) {
                Ok(policy) => Some(policy),
                Err(e) => {
                    warn!(error = %e, json = %s, "Failed to deserialize routing_policy_json, using None");
                    None
                }
            }
        })
    }

    pub(crate) async fn load_raw_routing_policy_json(
        &self,
        group_id: &str,
    ) -> ServiceResult<Option<Option<String>>> {
        let rows = self
            .db
            .query_with(
                &self.logical_db,
                "SELECT routing_policy_json FROM bcs_groups WHERE group_id = ? AND env = ?",
                vec![Value::from(group_id), Value::from(self.env.as_str())],
            )
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "load Group '{group_id}' routing_policy_json: {error}"
                ))
            })?;
        let Some(row) = rows.first() else {
            return Ok(None);
        };
        let json = db_get_column_opt(row, "routing_policy_json").map_err(|error| {
            ServiceError::InternalError(format!(
                "load Group '{group_id}' routing_policy_json: {error}"
            ))
        })?;
        Ok(Some(json))
    }

    pub(crate) fn deserialize_opening_message(
        json_str: Option<String>,
    ) -> ServiceResult<Option<OpeningMessage>> {
        match json_str.as_deref() {
            None => Ok(None),
            Some(json) => serde_json::from_str(json).map(Some).map_err(|error| {
                ServiceError::InternalError(format!("deserialize opening_message_json: {error}"))
            }),
        }
    }

    pub(crate) fn opening_message_from_row(
        row: &DbRow,
        group_id: &str,
    ) -> ServiceResult<Option<OpeningMessage>> {
        let json = db_get_column_opt(row, "opening_message_json").map_err(|error| {
            ServiceError::InternalError(format!(
                "read Group '{group_id}' opening_message_json: {error}"
            ))
        })?;
        Self::deserialize_opening_message(json).map_err(|error| {
            ServiceError::InternalError(format!(
                "read Group '{group_id}' opening_message_json: {error}"
            ))
        })
    }

    pub(crate) fn deserialize_participant_tags(json_str: Option<String>) -> Vec<String> {
        let Some(json_str) = json_str.filter(|value| !value.is_empty()) else {
            return Vec::new();
        };
        match serde_json::from_str::<Vec<String>>(&json_str) {
            Ok(tags) => tags,
            Err(error) => {
                warn!(%error, "failed to deserialize participant tags; using empty list");
                Vec::new()
            }
        }
    }

    /// Convert `HumanMentionNotifyMode` to the canonical string stored in
    /// `bcs_groups.human_mention_notify_mode`.
    pub(crate) fn human_mention_notify_mode_to_str(mode: HumanMentionNotifyMode) -> &'static str {
        match mode {
            HumanMentionNotifyMode::DriverBotOnly => "driver_bot_only",
            HumanMentionNotifyMode::All => "all",
            HumanMentionNotifyMode::None => "none",
        }
    }

    /// Parse `bcs_groups.human_mention_notify_mode`. Only SQL `NULL` (missing
    /// value) maps to `All` for rows that pre-date migration 030/031; the
    /// three canonical values pass through; every other non-NULL value,
    /// including the empty string, is an error instead of a silent default.
    pub(crate) fn parse_human_mention_notify_mode(
        raw: Option<&str>,
    ) -> ServiceResult<HumanMentionNotifyMode> {
        match raw {
            None => Ok(HumanMentionNotifyMode::All),
            Some("driver_bot_only") => Ok(HumanMentionNotifyMode::DriverBotOnly),
            Some("all") => Ok(HumanMentionNotifyMode::All),
            Some("none") => Ok(HumanMentionNotifyMode::None),
            Some(other) => Err(ServiceError::InternalError(format!(
                "unknown human_mention_notify_mode '{other}'"
            ))),
        }
    }

    /// Infallible decode of `human_mention_notify_mode` for the list/find
    /// projections that return `Vec<Group>`. Mirrors
    /// `message_view_scope_from_row`: decode/parse problems are logged with
    /// triage fields and normalized to the default in-memory only; the
    /// offending DB row is never rewritten.
    pub(crate) fn human_mention_notify_mode_from_row(
        row: &DbRow,
        group_id: &str,
    ) -> HumanMentionNotifyMode {
        let raw = match db_get_column_opt::<String>(row, "human_mention_notify_mode") {
            Ok(raw) => raw,
            Err(error) => {
                error!(%group_id, %error, "failed to decode group human_mention_notify_mode");
                return HumanMentionNotifyMode::All;
            }
        };
        match Self::parse_human_mention_notify_mode(raw.as_deref()) {
            Ok(mode) => mode,
            Err(error) => {
                error!(%group_id, %error, "invalid group human_mention_notify_mode; using default in-memory only");
                HumanMentionNotifyMode::All
            }
        }
    }
}
