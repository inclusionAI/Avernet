//! HTTP contract tests for session collection (collect/uncollect/collected list).
//!
//! Exercises the three endpoints added in Task 8:
//!   GET  /groups/{id}/sessions?collected=true&participant=<bot>
//!   POST /sessions/{sid}/collect
//!   DELETE /sessions/{sid}/collect
//!
//! Mirrors the app-state wiring from `session_list_contract.rs` with a
//! self-contained mock `CollectionMock` that records collections in memory.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{Body, to_bytes},
    http::{HeaderMap, Request, StatusCode},
};
use bcs_auth_api::{AuthError, UserIdentityInfo};
use bcs_bot::BotCore;
use bcs_group::GroupStore;
use bcs_http::{
    router::build_router,
    state::{HttpAppState, HttpUserIdentity, UserIdentityPort},
};
use bcs_service_api::{
    ActorKind, BotCapabilities, BotRegistryCoreService, CreateOrReactivateCommand,
    CreateOrReactivateOutcome, Group, GroupCoreService, Participant, ParticipantMode,
    ParticipantRole, Session, SessionKind, SessionManagementService, SessionStatus,
    SessionUseCaseError,
};
use bcs_services_container::Services;
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// mock service
// ---------------------------------------------------------------------------

#[derive(Default)]
struct CollectionMock {
    sessions: Mutex<Vec<Session>>,
    /// (session_id, bot_uuid) -> collected_at (synthetic monotonic ms).
    /// Monotonic so collect-event ordering is deterministic in tests.
    collected: Mutex<HashMap<(String, String), u64>>,
    collect_seq: AtomicU64,
}

impl CollectionMock {
    /// Synthetic collected_at: strictly increasing across collects so the
    /// collected list can be asserted by collect-event order.
    fn next_collected_at(&self) -> u64 {
        1_000 + self.collect_seq.fetch_add(1, Ordering::SeqCst)
    }
}

#[async_trait]
impl SessionManagementService for CollectionMock {
    async fn create_or_reactivate(
        &self,
        cmd: CreateOrReactivateCommand,
    ) -> Result<CreateOrReactivateOutcome, SessionUseCaseError> {
        let id = cmd
            .params
            .id
            .clone()
            .unwrap_or_else(|| format!("{}:abcdef12", cmd.group_id));
        let session = Session {
            id: id.clone(),
            group_id: cmd.group_id.clone(),
            session_title: cmd.params.session_title.clone(),
            env: None,
            status: SessionStatus::Running,
            session_kind: cmd.params.session_kind,
            participants: cmd.params.participants.clone(),
            group_version: cmd.params.group_version,
            caller_id: cmd.params.caller_id.clone(),
            input: cmd.params.input.clone(),
            output: None,
            error_message: None,
            activation_count: 1,
            message_visibility_version: cmd.params.message_visibility_version,
            caller_principal: cmd.params.caller_principal.clone(),
            created_by: cmd.params.created_by.clone(),
            created_at: 1,
            updated_at: 1,
            completed_at: None,
            meta: cmd.params.meta.clone(),
            current_msg_seq: 0,
            participant_join_seq: None,
            collected_at: None,
        };
        self.sessions.lock().await.push(session.clone());
        Ok(CreateOrReactivateOutcome {
            session,
            created: true,
        })
    }

    async fn get(&self, sid: &str) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(self
            .sessions
            .lock()
            .await
            .iter()
            .find(|s| s.id == sid)
            .cloned())
    }

    async fn belongs_to_group(
        &self,
        session_id: &str,
        group_id: &str,
    ) -> Result<bool, SessionUseCaseError> {
        Ok(self
            .sessions
            .lock()
            .await
            .iter()
            .any(|s| s.id == session_id && s.group_id == group_id))
    }

    async fn list_by_group(
        &self,
        group_id: &str,
        _status: Option<SessionStatus>,
        _offset: u64,
        _limit: u64,
        _title_contains: Option<&str>,
        _participant_id: Option<&str>,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        Ok(self
            .sessions
            .lock()
            .await
            .iter()
            .filter(|s| s.group_id == group_id)
            .cloned()
            .collect())
    }

    async fn complete_if_running(
        &self,
        _session_id: &str,
        _output: Option<Value>,
        _error: Option<String>,
    ) -> Result<Option<Session>, SessionUseCaseError> {
        Ok(None)
    }

    async fn add_participant(
        &self,
        _session_id: &str,
        _participant: Participant,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not used by collection tests")
    }

    async fn remove_participant(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not used by collection tests")
    }

    async fn update_participant_mode(
        &self,
        _session_id: &str,
        _bot_uuid: &str,
        _mode: ParticipantMode,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not used by collection tests")
    }

    async fn update_title(
        &self,
        _session_id: &str,
        _title: Option<String>,
    ) -> Result<Session, SessionUseCaseError> {
        unimplemented!("not used by collection tests")
    }

    async fn list_group_ids_by_session_participant(
        &self,
        _bot_uuid: &str,
    ) -> Result<Vec<String>, SessionUseCaseError> {
        Ok(Vec::new())
    }

    async fn delete(&self, _session_id: &str) -> Result<bool, SessionUseCaseError> {
        Ok(false)
    }

    // ── collection overrides ──────────────────────────────────────────

    async fn collect(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<(), SessionUseCaseError> {
        let exists = self
            .sessions
            .lock()
            .await
            .iter()
            .any(|s| s.id == session_id);
        if !exists {
            return Err(SessionUseCaseError::NotFound(session_id.to_string()));
        }
        self.collected
            .lock()
            .await
            // Idempotent: a repeat collect keeps the original event time.
            .entry((session_id.to_string(), bot_uuid.to_string()))
            .or_insert_with(|| self.next_collected_at());
        Ok(())
    }

    async fn uncollect(
        &self,
        session_id: &str,
        bot_uuid: &str,
    ) -> Result<(), SessionUseCaseError> {
        let exists = self
            .sessions
            .lock()
            .await
            .iter()
            .any(|s| s.id == session_id);
        if !exists {
            return Err(SessionUseCaseError::NotFound(session_id.to_string()));
        }
        self.collected
            .lock()
            .await
            .remove(&(session_id.to_string(), bot_uuid.to_string()));
        Ok(())
    }

    async fn list_collected_by_group(
        &self,
        group_id: &str,
        bot_uuid: &str,
        _status: Option<SessionStatus>,
        _title_contains: Option<&str>,
        _offset: u64,
        _limit: u64,
    ) -> Result<Vec<Session>, SessionUseCaseError> {
        let sessions = self.sessions.lock().await;
        let collected = self.collected.lock().await;
        let mut out: Vec<Session> = sessions
            .iter()
            .filter(|s| {
                s.group_id == group_id
                    && collected.contains_key(&(s.id.clone(), bot_uuid.to_string()))
            })
            .cloned()
            .map(|mut s| {
                s.collected_at = collected
                    .get(&(s.id.clone(), bot_uuid.to_string()))
                    .copied()
                    .or(Some(s.created_at));
                s
            })
            .collect();
        // Newest collect event first (COALESCE(collected_at, created_at)).
        out.sort_by_key(|s| std::cmp::Reverse(s.collected_at.unwrap_or(s.created_at)));
        Ok(out)
    }

    async fn collected_at_map(
        &self,
        session_ids: &[&str],
        bot_uuid: &str,
    ) -> Result<Vec<(String, u64)>, SessionUseCaseError> {
        let collected = self.collected.lock().await;
        Ok(session_ids
            .iter()
            .filter_map(|sid| {
                let ts = collected
                    .get(&(sid.to_string(), bot_uuid.to_string()))
                    .copied()?;
                Some((sid.to_string(), ts))
            })
            .collect())
    }
}

// ---------------------------------------------------------------------------
// tests
// ---------------------------------------------------------------------------






// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

async fn test_app() -> (
    axum::Router,
    Arc<CollectionMock>,
    TempDir,
    Arc<BotCore>,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    register_bot(&registry, "driver-bot", "Driver").await;
    registry
        .store_token_mapping("driver-token".to_string(), "driver-bot".to_string())
        .await;
    // A second, distinct bot used to assert BOLA on the collected-list filter.
    register_bot(&registry, "intruder-bot", "Intruder").await;
    registry
        .store_token_mapping("intruder-token".to_string(), "intruder-bot".to_string())
        .await;

    let group_store = Arc::new(GroupStore::new());
    group_store
        .upsert(Group::new(
            "group-1",
            "driver-bot",
            vec![Participant {
                bot_uuid: "driver-bot".to_string(),
                bot_name: Some("Driver".to_string()),
                kind: None,
                role: ParticipantRole::Driver,
                actor_kind: ActorKind::Bot,
                mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
                tags: Vec::new(),
                message_view_scope: bcs_domain::MessageViewScope::Full,
            }],
        ))
        .await
        .unwrap();

    let sessions = Arc::new(CollectionMock::default());

    let mut services = Services::noop();
    services.registry = registry.clone();
    services.group = group_store;
    services.session_management = sessions.clone();

    let app = build_router(HttpAppState::new(services));
    (app, sessions, temp_dir, registry)
}

async fn human_app(
    staff_no: &str,
    owned_bot_ids: &[&str],
) -> (
    axum::Router,
    Arc<CollectionMock>,
    TempDir,
    Arc<BotCore>,
) {
    let temp_dir = TempDir::new().unwrap();
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));

    // Register the human's owned bots and set created_by.
    for bot_id in owned_bot_ids {
        register_bot(&registry, bot_id, bot_id).await;
        registry
            .save_created_by(bot_id, staff_no, true)
            .await
            .unwrap();
    }

    // Register an unauthorized bot (not owned by this human).
    register_bot(&registry, "unauthorized-bot", "Unauthorized").await;
    registry
        .save_created_by("unauthorized-bot", "someone-else", true)
        .await
        .unwrap();

    let group_store = Arc::new(GroupStore::new());
    // LIVE authority chain mirroring the fixture
    // `created_by` rows (bot-authority plan Task 6.5).
    let authority = bcs_test_support::lazy_authority_chain().hook;
    for bot_id in owned_bot_ids {
        authority.seed_owner(bot_id, staff_no);
    }
    authority.seed_owner("unauthorized-bot", "someone-else");
    let driver = owned_bot_ids.first().copied().unwrap_or("owned-bot");
    let participants: Vec<Participant> = owned_bot_ids
        .iter()
        .map(|bid| Participant {
            bot_uuid: bid.to_string(),
            bot_name: Some(bid.to_string()),
            kind: None,
            role: if *bid == driver {
                ParticipantRole::Driver
            } else {
                ParticipantRole::Consultant
            },
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
            tags: Vec::new(),
            message_view_scope: bcs_domain::MessageViewScope::Full,
        })
        .collect();
    group_store
        .upsert(Group::new("group-1", driver, participants))
        .await
        .unwrap();

    let sessions = Arc::new(CollectionMock::default());

    let mut services = Services::noop();
    services.registry = registry.clone();
    services.group = group_store;
    services.session_management = sessions.clone();
    services.bot_authority_hook = authority;

    let identity_port: Arc<dyn UserIdentityPort + Send + Sync> = Arc::new(StaticHumanIdentity {
        staff_no: Some(staff_no.to_string()),
    });
    let app = build_router(HttpAppState::new(services).with_user_identity(identity_port));
    (app, sessions, temp_dir, registry)
}

async fn register_bot(registry: &BotCore, bot_id: &str, name: &str) {
    registry
        .register(
            bot_id.to_string(),
            BotCapabilities {
                name: Some(name.to_string()),
                summary: Some(format!("{name} summary")),
                visibility: "public".to_string(),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
}

#[derive(Default)]
struct StaticHumanIdentity {
    staff_no: Option<String>,
}

#[async_trait]
impl UserIdentityPort for StaticHumanIdentity {
    async fn extract(
        &self,
        _headers: &HeaderMap,
        _uri: &axum::http::Uri,
    ) -> Option<HttpUserIdentity> {
        self.staff_no.as_ref().map(|sn| HttpUserIdentity {
            staff_no: Some(sn.clone()),
            nick_name: Some("Test".to_string()),
        })
    }

    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Ok("noop-identity".to_string())
    }

    async fn get_identity_by_token(
        &self,
        _token: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<UserIdentityInfo>, AuthError> {
        Ok(None)
    }
}


/// Push a collectable session into the mock with
/// `participant` as its Bot participant.
async fn push_session(
    mock: &Arc<CollectionMock>,
    sid: &str,
    participant: &str,
) {
    mock.sessions.lock().await.push(Session {
        id: sid.to_string(),
        group_id: "group-1".to_string(),
        session_title: None,
        env: None,
        status: SessionStatus::Running,
        session_kind: SessionKind::Chat,
        participants: vec![Participant {
            bot_uuid: participant.to_string(),
            bot_name: Some(participant.to_string()),
            kind: None,
            role: ParticipantRole::Consultant,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::default_for(ActorKind::Bot)),
            tags: Vec::new(),
            message_view_scope: bcs_domain::MessageViewScope::Full,
        }],
        group_version: Some(1),
        caller_id: None,
        input: None,
        output: None,
        error_message: None,
        activation_count: 1,
        message_visibility_version: 1,
        caller_principal: None,
        created_by: None,
        created_at: 1,
        updated_at: 1,
        completed_at: None,
        meta: None,
        current_msg_seq: 0,
        participant_join_seq: None,
        collected_at: None,
    });
}

#[path = "session_collection_contract/collection.rs"]
mod collection;
#[path = "session_collection_contract/authorization.rs"]
mod authorization;
#[path = "session_collection_contract/ordering.rs"]
mod ordering;
