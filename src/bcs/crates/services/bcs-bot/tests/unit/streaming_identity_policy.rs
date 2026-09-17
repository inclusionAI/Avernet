use super::*;
use bcs_service_api::port::repo::BotMemoryIdentity;
use std::sync::{
    atomic::{AtomicUsize, Ordering::SeqCst},
    Mutex,
};
use std::time::{Duration, Instant};

struct Facts {
    memory: Option<BotMemoryIdentity>,
    stored: Option<BotIdentity>,
    fail_read: bool,
    reads: Arc<AtomicUsize>,
    applied: Arc<Mutex<Option<BotIdentityUpdate>>>,
}
#[async_trait]
impl BotIdentityOperationPort for Facts {
    async fn token_owner(&mut self, token: &str) -> ServiceResult<Option<String>> {
        Ok(self
            .memory
            .as_ref()
            .map(|m| &m.identity)
            .into_iter()
            .chain(self.stored.as_ref())
            .find(|identity| identity.token.as_deref() == Some(token))
            .map(|identity| identity.id.clone()))
    }
    async fn lock_identity(&mut self, _: &str) -> ServiceResult<Option<BotMemoryIdentity>> {
        Ok(self.memory.clone())
    }
    async fn stored_identity(&mut self) -> ServiceResult<Option<BotIdentity>> {
        self.reads.fetch_add(1, SeqCst);
        if self.fail_read {
            return Err(ServiceError::InternalError("DB unavailable".into()));
        }
        Ok(self.stored.clone())
    }
    async fn apply(self: Box<Self>, update: BotIdentityUpdate) -> ServiceResult<()> {
        *self.applied.lock().unwrap() = Some(update);
        Ok(())
    }
}
fn identity(token: &str, deleted: bool) -> BotIdentity {
    BotIdentity {
        id: "bot-a".into(),
        token: Some(token.into()),
        deleted,
        capabilities: BotCapabilities {
            name: Some("kept".into()),
            agent_token: Some("runtime".into()),
            ..Default::default()
        },
        env: None,
        created_by: None,
        actor_kind: bcs_service_api::ActorKind::Bot,
        status: ActorStatus::Online,
    }
}

#[tokio::test]
async fn core_owns_temporary_expiry_and_credential_rules() {
    // age, connected, supplied token, expected new/reconnect/error
    for (age, connected, supplied, outcome) in [
        (10, false, None, "registered"),
        (10, false, Some("wrong"), "registered"),
        (10, false, Some("old"), "reconnect"),
        (301, false, None, "new"),
        (301, false, Some("old"), "new"),
        (301, true, None, "connected"),
        (301, true, Some("wrong"), "connected"),
        (301, true, Some("old"), "reconnect"),
    ] {
        let applied = Arc::new(Mutex::new(None));
        let reads = Arc::new(AtomicUsize::new(0));
        let result = BotCore::connect_streaming_with_operation(
            Box::new(Facts {
                memory: Some(BotMemoryIdentity {
                    identity: identity("old", false),
                    connected,
                    last_heartbeat: Instant::now() - Duration::from_secs(age),
                }),
                stored: None,
                fail_read: false,
                reads: reads.clone(),
                applied: applied.clone(),
            }),
            BotConnectParams {
                bot_id: Some("bot-a".into()),
                token: supplied.map(str::to_owned),
                ..Default::default()
            },
        )
        .await;
        match outcome {
            "registered" => assert!(matches!(result, Err(ConnectError::AlreadyRegistered(_)))),
            "connected" => assert!(matches!(result, Err(ConnectError::AlreadyConnected(_)))),
            expected => {
                let result = result.unwrap();
                assert_eq!(result.is_new, expected == "new");
                assert_eq!(result.token == "old", expected == "reconnect");
                let update = applied.lock().unwrap();
                let update = update.as_ref().unwrap();
                assert_eq!(
                    update.identity.capabilities.name.is_none(),
                    expected == "new"
                );
                assert!(!update.replace_persistent_token);
            }
        }
        assert_eq!(
            applied.lock().unwrap().is_some(),
            matches!(outcome, "new" | "reconnect")
        );
        assert_eq!(
            reads.load(SeqCst),
            usize::from(matches!(outcome, "new" | "reconnect"))
        );
    }
}

#[tokio::test]
async fn core_never_reclaims_durable_deleted_or_unknown_identity() {
    for (stored, fail_read, expected) in [
        (Some(identity("old", false)), false, "registered"),
        (Some(identity("old", true)), false, "registered"),
        (None, true, "internal"),
    ] {
        let applied = Arc::new(Mutex::new(None));
        let result = BotCore::connect_streaming_with_operation(
            Box::new(Facts {
                memory: Some(BotMemoryIdentity {
                    identity: identity("old", false),
                    connected: false,
                    last_heartbeat: Instant::now() - Duration::from_secs(301),
                }),
                stored,
                fail_read,
                reads: Arc::new(AtomicUsize::new(0)),
                applied: applied.clone(),
            }),
            BotConnectParams {
                bot_id: Some("bot-a".into()),
                ..Default::default()
            },
        )
        .await;
        if expected == "internal" {
            assert!(matches!(result, Err(ConnectError::InternalError(_))));
        } else {
            assert!(matches!(result, Err(ConnectError::AlreadyRegistered(_))));
        }
        assert!(applied.lock().unwrap().is_none());
    }
}
