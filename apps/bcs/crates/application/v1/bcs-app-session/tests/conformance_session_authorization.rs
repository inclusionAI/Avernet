//! Rule 25 driver: runs the shared conformance
//! harness against the REAL
//! `SessionAuthorizationCoreImpl` composed over the
//! REAL memory session store (bcs-app-session owns
//! the composition; `SessionAuthorizationCoreImpl`
//! lives in `bcs-session`).

use std::sync::Arc;

use async_trait::async_trait;

use bcs_domain::{ActorKind, MessageViewScope, ParticipantMode, ParticipantRole};
use bcs_service_api::core::SessionAuthorizationCoreService;
use bcs_service_api::port::repo::{NewSessionParams, SessionRepoPort};
use bcs_service_api::{Participant, SessionKind};
use bcs_session::SessionAuthorizationCoreImpl;
use bcs_session_store::MemorySessionRepo;
use bcs_test_support::contract::session_authorization::{
    SessionAuthorizationCoreFixture, SessionFacts,
    session_authorization_core_service_contract_tests,
};

fn human_participant(actor_id: &str) -> Participant {
    Participant {
        bot_uuid: actor_id.to_string(),
        bot_name: Some(actor_id.to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Human,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Participant,
    }
}


fn bot_participant(bot_id: &str, role: ParticipantRole) -> Participant {
    Participant {
        bot_uuid: bot_id.to_string(),
        bot_name: Some(bot_id.to_string()),
        kind: None,
        role,
        actor_kind: ActorKind::Bot,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: MessageViewScope::Participant,
    }
}

/// The Real core over the real memory session store;
/// the seeded facts go through the store's REAL write
/// paths (`create` / `add_participant`).
struct MemoryCoreFixture {
    repo: Arc<dyn SessionRepoPort>,
    core: Arc<dyn SessionAuthorizationCoreService>,
    session_id: std::sync::Mutex<Option<String>>,
}

impl MemoryCoreFixture {
    async fn new() -> Self {
        let repo = Arc::new(MemorySessionRepo::new());
        let core = Arc::new(SessionAuthorizationCoreImpl::new(
            repo.clone(),
        ));
        Self {
            repo,
            core,
            session_id: std::sync::Mutex::new(None),
        }
    }
}

#[async_trait]
impl SessionAuthorizationCoreFixture for MemoryCoreFixture {
    fn core(&self) -> Arc<dyn SessionAuthorizationCoreService> {
        self.core.clone()
    }

    async fn commit_facts(&self, variant: u8) -> SessionFacts {
        match variant {
            0 => {
                let session = self
                    .repo
                    .create(
                        "harness-group",
                        NewSessionParams {
                            session_kind: SessionKind::Chat,
                            participants: vec![
                                bot_participant(
                                    "harness-driver",
                                    ParticipantRole::Driver,
                                ),
                                human_participant("human_harness"),
                            ],
                            created_by: Some(
                                "harness-driver".to_string(),
                            ),
                            ..Default::default()
                        },
                    )
                    .await
                    .expect("seed session");
                *self
                    .session_id
                    .lock()
                    .expect("fixture state") =
                    Some(session.id.clone());
                self.facts_of(&session.id).await
            }
            _ => {
                let session_id = self
                    .session_id
                    .lock()
                    .expect("fixture state")
                    .clone()
                    .expect("variant 1 after variant 0");
                self.repo
                    .add_participant(
                        &session_id,
                        bot_participant(
                            "harness-worker",
                            ParticipantRole::Worker,
                        ),
                    )
                    .await
                    .expect("add participant");
                self.facts_of(&session_id).await
            }
        }
    }

    async fn delete_session(&self, session_id: &str) {
        self.repo
            .delete(session_id)
            .await
            .expect("delete session");
    }
}

impl MemoryCoreFixture {
    /// The repo's own committed projection: the facts
    /// summary comes from the SAME store write path the
    /// core reads — no invented expectations.
    async fn facts_of(&self, session_id: &str) -> SessionFacts {
        let snapshot = self
            .repo
            .read_authorization_snapshot(session_id)
            .await
            .expect("repo projection read")
            .expect("the seeded session must project");
        SessionFacts {
            session_id: snapshot.id.clone(),
            group_id: snapshot.group_id.clone(),
            env: snapshot.env.clone(),
            participants: snapshot.participants.len(),
        }
    }
}

/// The snapshot read contract on the REAL composed
/// core.
#[tokio::test]
async fn session_authorization_core_service_contract_composed() {
    let fixture = MemoryCoreFixture::new().await;
    session_authorization_core_service_contract_tests(&fixture)
        .await;
}
