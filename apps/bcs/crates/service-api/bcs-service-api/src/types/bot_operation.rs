//! Ordinary business-operation audit identity and record description
//! (spec §12.5).
//!
//! Pure types only: the application builds a required
//! [`BotOperationContext`] after authentication and effective-actor
//! selection, and each owning store persists a
//! [`BotActionAuditRecord`] in the SAME transaction as the business
//! mutation. There is no global audit service here; concrete SQL,
//! memory critical sections and stores are later tasks' work
//! (plan Tasks 2/9—12).
//!
//! Key invariants encoded by this module:
//! - operator kind/id are ONLY projected from the typed
//!   [`BotOperationActor`]; they are never taken from request bodies;
//! - Human and Bot-only are distinct legitimate branches; a `None`
//!   operator user id means genuinely no Human, never a missing
//!   parameter fallback;
//! - `step_key` is generated internally from the controlled
//!   action/resource/phase vocabulary, never from client input;
//! - records are append-only: the unique `(env, operation_id,
//!   step_key)` slot plus identical application-authored content means
//!   a legitimate retry keeps the FIRST database timestamps (which are
//!   DB-generated and not part of this record at all); a same-slot
//!   record with different content must be a conflict.
//!
//! Role lifecycles (manager mutations, ownership transfer, team sync)
//! are NOT ordinary business audits — they use
//! `bcs_domain::AuditActor` and dedicated ledgers
//! (`bot_manager_changes` / transfer receipts).

use serde::{Deserialize, Serialize};

/// The typed identity of whoever operates an ordinary business write.
///
/// Variants are exhaustive and enum-branched on purpose (spec §12.5):
/// `None` operator user id on Bot/System branches means "no Human was
/// involved", not "parameters missing" — and Bot-only operation NEVER
/// grants eligibility for Human-only manager/transfer APIs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum BotOperationActor {
    /// A verified Human. `user_id` is the trusted User ID of the real
    /// operator; `effective_actor_id` is the actor the application's
    /// authorization selected (the Human themselves or a Bot they are
    /// authorized to act for).
    Human {
        user_id: String,
        effective_actor_id: String,
    },
    /// An authenticated Bot operating as itself only. `bot_id` is the
    /// verified Bot identity; there is no Human operator.
    Bot { bot_id: String },
    /// An independent system action (e.g. runtime cleanup with no
    /// recorded historical operator). `system_id` is a fixed system
    /// identifier, never a User ID.
    System {
        system_id: String,
        effective_actor_id: String,
    },
}

impl BotOperationActor {
    /// `operator_kind` column projection: `human` / `bot` / `system`.
    pub fn operator_kind(&self) -> &'static str {
        match self {
            Self::Human { .. } => "human",
            Self::Bot { .. } => "bot",
            Self::System { .. } => "system",
        }
    }

    /// `operator_id` column projection: the trusted Human User ID, the
    /// verified Bot ID, or the fixed system identifier.
    pub fn operator_id(&self) -> &str {
        match self {
            Self::Human { user_id, .. } => user_id.as_str(),
            Self::Bot { bot_id } => bot_id.as_str(),
            Self::System { system_id, .. } => system_id.as_str(),
        }
    }

    /// `operator_user_id` column projection: `Some` ONLY for the Human
    /// branch; Bot-only and system actions have no operator User ID.
    pub fn operator_user_id(&self) -> Option<&str> {
        match self {
            Self::Human { user_id, .. } => Some(user_id.as_str()),
            Self::Bot { .. } | Self::System { .. } => None,
        }
    }

    /// `effective_actor_id` column projection: the actor the
    /// application's authorization selected for this operation.
    pub fn effective_actor_id(&self) -> &str {
        match self {
            Self::Human {
                effective_actor_id, ..
            } => effective_actor_id.as_str(),
            Self::Bot { bot_id } => bot_id.as_str(),
            Self::System {
                effective_actor_id, ..
            } => effective_actor_id.as_str(),
        }
    }
}

/// REQUIRED propagation carrier of the audit identity (spec §12.5).
///
/// Write commands that need an audit trail receive this as a plain
/// value (`BotOperationContext`), never as `Option<Context>`: audit
/// must not be silently skipped by production callers. History rows may
/// legitimately have no context; NEW delegation commands must provide
/// one. operation_id is service-generated (or a client-supplied
/// idempotency key where a use case defines one); the actor is built by
/// the application after authentication and effective-actor selection.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotOperationContext {
    /// Stable identifier grouping all audit records of one operation.
    pub operation_id: String,
    /// The typed operator identity; columns are projected from it.
    pub actor: BotOperationActor,
}

/// Controlled vocabulary of audited resource kinds (spec §12.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotActionResourceKind {
    /// Bot configuration / status / control-plane state.
    Bot,
    /// Groups, sessions belonging to group coordination, participants.
    Group,
    /// Session-level write state (e.g. collect favorites).
    Session,
    /// Session files: mutation/share/deletion.
    SessionFile,
    /// Workspace read/update state.
    Workspace,
    /// Message sending / aborting control.
    Message,
    /// Friend edge writes initiated by the cutover paths.
    Friend,
    /// Invitation writes.
    Invitation,
}

impl BotActionResourceKind {
    /// Wire/step-key label (snake_case, matches the serde encoding).
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Bot => "bot",
            Self::Group => "group",
            Self::Session => "session",
            Self::SessionFile => "session_file",
            Self::Workspace => "workspace",
            Self::Message => "message",
            Self::Friend => "friend",
            Self::Invitation => "invitation",
        }
    }
}

impl BotActionKind {
    /// Whether this action performs an external/runtime side effect
    /// AFTER a persisted `admitted` record (spec §12.5).
    pub fn has_external_side_effect(&self) -> bool {
        matches!(self, Self::Share | Self::Launch | Self::Send | Self::Abort)
    }

    /// Wire/step-key label (snake_case, matches the serde encoding).
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Create => "create",
            Self::Update => "update",
            Self::Delete => "delete",
            Self::Share => "share",
            Self::Collect => "collect",
            Self::Launch => "launch",
            Self::Send => "send",
            Self::Abort => "abort",
            Self::Invite => "invite",
        }
    }
}

/// Controlled vocabulary of audited actions (spec §12.5).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotActionKind {
    /// Create a resource.
    Create,
    /// Update configuration/state.
    Update,
    /// Delete/skip a resource.
    Delete,
    /// Share mutation (files etc.).
    Share,
    /// Collect/uncollect favorites.
    Collect,
    /// Launch a persisted run.
    Launch,
    /// Send a persisted delivery/job.
    Send,
    /// Abort a persisted delivery/run.
    Abort,
    /// Friend/invitation request write.
    Invite,
}

/// Audit phase within one operation (spec §12.5, `phase` column).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BotActionAuditPhase {
    /// Persisted together with the business mutation it describes.
    Applied,
    /// Persisted after authentication, BEFORE any external side effect.
    Admitted,
    /// Persisted after external I/O succeeded.
    Completed,
    /// Persisted on explicit external failure.
    Failed,
    /// Side effect happened but the result is indeterminate.
    Unknown,
}

impl BotActionAuditPhase {
    /// Wire/column label (snake_case, matches the serde encoding).
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Applied => "applied",
            Self::Admitted => "admitted",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Unknown => "unknown",
        }
    }
}

/// Stable in-operation step key composed of the controlled
/// action/resource/phase vocabulary (e.g. `send/message/admitted`).
///
/// Application/domain code calls this to generate `step_key`; clients
/// can never specify their own step keys. Steps per operation must be
/// distinct (`admitted` → `completed` etc.); the same key is reused
/// for an in-command retry of the same logical step.
pub fn stable_step_key(
    action: BotActionKind,
    resource_kind: BotActionResourceKind,
    phase: BotActionAuditPhase,
) -> String {
    format!(
        "{}/{}/{}",
        action.as_str(),
        resource_kind_label(resource_kind),
        phase_label(phase),
    )
}

fn resource_kind_label(kind: BotActionResourceKind) -> &'static str {
    kind.as_str()
}

/// Wire/step-key label of the audit phase (serde name).
fn phase_label(phase: BotActionAuditPhase) -> &'static str {
    phase.as_str()
}

/// One audit record of an ordinary business operation
/// (spec §12.5 `bcs_bot_action_audits` application-authored content).
///
/// Field-by-field semantics (spec §12.5 table):
/// - `audit_id`, `operation_id`, `env`: service-generated ids and the
///   assembly environment; `(env, operation_id, step_key)` is unique
///   per store;
/// - `operator`: the typed actor; the row's `operator_kind`,
///   `operator_id`, `operator_user_id` and `effective_actor_id`
///   columns are projected from it ONLY;
/// - `resource_kind`/`resource_id`/`action`/`phase`: the controlled
///   vocabularies; `step_key` covers action/resource/phase and
///   request bodies are never persisted;
/// - `reason_code`: optional fixed machine reason; raw storage error
///   text, message bodies, file content and tokens are never recorded;
/// - `gmt_create`/`gmt_modified` are DATABASE-generated non-null
///   timestamps, deliberately absent from this struct: records are
///   append-only, so a legitimate retry keeps the first row's
///   timestamps.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BotActionAuditRecord {
    /// Service-generated record id.
    pub audit_id: String,
    /// Assembly environment.
    pub env: String,
    /// Operation grouping id (shared with the business command).
    pub operation_id: String,
    /// Stable step key within the operation (see [`stable_step_key`]).
    pub step_key: String,
    /// Typed operator; the only source of operator columns.
    pub operator: BotOperationActor,
    /// Controlled resource kind.
    pub resource_kind: BotActionResourceKind,
    /// Exact resource identifier.
    pub resource_id: String,
    /// Controlled action.
    pub action: BotActionKind,
    /// Phase within the operation.
    pub phase: BotActionAuditPhase,
    /// Optional fixed machine reason (never raw error text).
    pub reason_code: Option<String>,
}

impl BotActionAuditRecord {
    /// Build a record with an internally generated stable step key.
    pub fn new(
        audit_id: impl Into<String>,
        env: impl Into<String>,
        operation_id: impl Into<String>,
        operator: BotOperationActor,
        resource_kind: BotActionResourceKind,
        resource_id: impl Into<String>,
        action: BotActionKind,
        phase: BotActionAuditPhase,
        reason_code: Option<String>,
    ) -> Self {
        Self {
            audit_id: audit_id.into(),
            env: env.into(),
            operation_id: operation_id.into(),
            step_key: stable_step_key(action, resource_kind, phase),
            operator,
            resource_kind,
            resource_id: resource_id.into(),
            action,
            phase,
            reason_code,
        }
    }

    /// Whether two records occupy the same unique persisted slot
    /// (`(env, operation_id, step_key)`).
    pub fn same_slot(&self, other: &Self) -> bool {
        self.env == other.env
            && self.operation_id == other.operation_id
            && self.step_key == other.step_key
    }

    /// Whether a same-slot record CONFLICTS: same
    /// `(env, operation_id, step_key)` but different application-
    /// authored content. Such an insert must be rejected, never
    /// overwrite a different operator/action.
    ///
    /// The comparison is over the whole application-authored record;
    /// the DB-generated `gmt_create`/`gmt_modified` timestamps are not
    /// part of the record (by construction), so a legitimate retry of
    /// identical content is NOT a conflict and the first row's
    /// timestamps are preserved.
    pub fn content_conflicts(&self, other: &Self) -> bool {
        self.same_slot(other) && self != other
    }
}

/// Project the typed audit identity from a transport-neutral
/// [`super::EventActor`] that an authenticated boundary already chose.
///
/// `human_<id>` maps to the Human branch (the User ID IS the trusted
/// staff number the boundary authenticated); Bot actors keep their
/// verified Bot identity without any Human; System/App event actors map
/// to the System branch with a fixed system identifier — never a forged
/// Human. The `operation_id` is service-generated per call.
pub fn operation_context_from_event_actor(
    operation_id: impl Into<String>,
    actor: &super::EventActor,
) -> BotOperationContext {
    BotOperationContext {
        operation_id: operation_id.into(),
        actor: BotOperationActor::from_event_actor(actor),
    }
}

impl BotOperationActor {
    /// Same projection as [`operation_context_from_event_actor`] but for
    /// the actor alone.
    pub fn from_event_actor(actor: &super::EventActor) -> Self {
        match actor.actor_type {
            super::EventActorType::Human => Self::Human {
                user_id: actor
                    .id
                    .strip_prefix("human_")
                    .unwrap_or(&actor.id)
                    .to_string(),
                effective_actor_id: actor.id.clone(),
            },
            super::EventActorType::Bot => Self::Bot { bot_id: actor.id.clone() },
            super::EventActorType::System | super::EventActorType::App => Self::System {
                system_id: actor.id.clone(),
                effective_actor_id: actor.id.clone(),
            },
        }
    }
}

#[cfg(test)]
mod tests {
    //! Pure-type test area for the ordinary business audit contract
    //! (plan Task 1). No store, no SQL, no I/O.

    use super::*;

    #[test]
    fn human_actor_projects_both_identities() {
        let human = BotOperationActor::Human {
            user_id: "a".into(),
            effective_actor_id: "bot-x".into(),
        };
        assert_eq!(human.operator_user_id(), Some("a"));
        assert_eq!(human.effective_actor_id(), "bot-x");
        assert_eq!(human.operator_kind(), "human");
        assert_eq!(human.operator_id(), "a");
    }

    #[test]
    fn bot_only_actor_has_no_operator_user_id() {
        let bot = BotOperationActor::Bot {
            bot_id: "bot-x".into(),
        };
        assert_eq!(bot.operator_user_id(), None);
        assert_eq!(bot.effective_actor_id(), "bot-x");
        assert_eq!(bot.operator_kind(), "bot");
        assert_eq!(bot.operator_id(), "bot-x");
    }

    #[test]
    fn system_actor_keeps_fixed_system_identity() {
        let system = BotOperationActor::System {
            system_id: "runtime-cleanup".into(),
            effective_actor_id: "runtime-cleanup".into(),
        };
        assert_eq!(system.operator_user_id(), None);
        assert_eq!(system.operator_kind(), "system");
        assert_eq!(system.operator_id(), "runtime-cleanup");
        assert_eq!(system.effective_actor_id(), "runtime-cleanup");
    }

    #[test]
    fn unknown_operator_kind_is_rejected_at_decode() {
        assert!(
            serde_json::from_str::<BotOperationActor>(
                r#"{"kind":"admin","user_id":"a","effective_actor_id":"b"}"#
            )
            .is_err()
        );
    }

    #[test]
    fn operation_context_roundtrips_and_is_required_value() {
        let ctx = BotOperationContext {
            operation_id: "op-1".into(),
            actor: BotOperationActor::Human {
                user_id: "a".into(),
                effective_actor_id: "bot-x".into(),
            },
        };
        let text = serde_json::to_string(&ctx).unwrap();
        let back: BotOperationContext = serde_json::from_str(&text).unwrap();
        assert_eq!(back, ctx);
    }

    #[test]
    fn phase_vocabulary_is_stable() {
        let phases = [
            (BotActionAuditPhase::Applied, "\"applied\""),
            (BotActionAuditPhase::Admitted, "\"admitted\""),
            (BotActionAuditPhase::Completed, "\"completed\""),
            (BotActionAuditPhase::Failed, "\"failed\""),
            (BotActionAuditPhase::Unknown, "\"unknown\""),
        ];
        for (phase, text) in phases {
            assert_eq!(serde_json::to_string(&phase).unwrap(), text);
            assert_eq!(
                serde_json::from_str::<BotActionAuditPhase>(text).unwrap(),
                phase
            );
        }
        assert!(serde_json::from_str::<BotActionAuditPhase>("\"done\"").is_err());
    }

    #[test]
    fn step_key_is_generated_from_controlled_vocabulary() {
        let key = stable_step_key(
            BotActionKind::Send,
            BotActionResourceKind::Message,
            BotActionAuditPhase::Admitted,
        );
        assert_eq!(key, "send/message/admitted");
        let applied = stable_step_key(
            BotActionKind::Update,
            BotActionResourceKind::Bot,
            BotActionAuditPhase::Applied,
        );
        assert_ne!(applied, key, "different step must have a different key");
        let completed = stable_step_key(
            BotActionKind::Send,
            BotActionResourceKind::Message,
            BotActionAuditPhase::Completed,
        );
        assert_ne!(completed, key, "admitted and completed are distinct steps");
    }

    #[test]
    fn same_step_key_identical_content_is_not_a_conflict() {
        let record = |operator: BotOperationActor| {
            BotActionAuditRecord::new(
                "audit-1",
                "dev",
                "op-1",
                operator,
                BotActionResourceKind::Session,
                "s-1",
                BotActionKind::Collect,
                BotActionAuditPhase::Applied,
                None,
            )
        };
        let first = record(BotOperationActor::Bot {
            bot_id: "bot-x".into(),
        });
        // A legitimate retry of the same content keeps the original
        // (first, DB-generated) timestamps — which are not part of the
        // record — so this is not a conflict.
        let retry = first.clone();
        assert!(retry.same_slot(&first));
        assert!(!retry.content_conflicts(&first));
    }

    #[test]
    fn same_step_key_different_content_must_conflict() {
        let base = BotActionAuditRecord::new(
            "audit-1",
            "dev",
            "op-1",
            BotOperationActor::Human {
                user_id: "a".into(),
                effective_actor_id: "bot-x".into(),
            },
            BotActionResourceKind::Bot,
            "bot-x",
            BotActionKind::Update,
            BotActionAuditPhase::Applied,
            None,
        );
        // Different operator under the same step key.
        let other_operator = BotActionAuditRecord {
            operator: BotOperationActor::Human {
                user_id: "b".into(),
                effective_actor_id: "bot-x".into(),
            },
            ..base.clone()
        };
        assert!(other_operator.content_conflicts(&base));
        // Different action under the same step key (forged record).
        let other_action = BotActionAuditRecord {
            action: BotActionKind::Delete,
            ..base.clone()
        };
        assert!(other_action.content_conflicts(&base));
        // A different operation_id/step_key is simply a different slot.
        let other_op = BotActionAuditRecord {
            operation_id: "op-2".into(),
            ..base.clone()
        };
        assert!(!other_op.content_conflicts(&base));
        assert!(!other_op.same_slot(&base));
    }

    #[test]
    fn external_side_effect_actions_need_admitted_before_io() {
        assert!(BotActionKind::Send.has_external_side_effect());
        assert!(BotActionKind::Launch.has_external_side_effect());
        assert!(BotActionKind::Abort.has_external_side_effect());
        assert!(BotActionKind::Share.has_external_side_effect());
        assert!(!BotActionKind::Collect.has_external_side_effect());
        assert!(!BotActionKind::Update.has_external_side_effect());
    }
}
