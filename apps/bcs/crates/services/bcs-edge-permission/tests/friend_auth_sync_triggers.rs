//! Real ConnectService + SQLite stores -> external friend-auth port.
//! TC HTTP filtering/splitting is tested separately by the bootstrap adapter.
use std::sync::Arc;
use async_trait::async_trait;
use bcs_edge_permission::DbConnectService;
use bcs_service_api::application::connect::{ConnectService, ConnectStatus};
use bcs_service_api::port::{
    FriendAuthSyncAction, FriendAuthSyncCommand, FriendAuthSyncPort,
    NoopFriendConnectNotificationPort,
};
use bcs_service_api::{RequestAuthHeaders, ServiceResult};
use tokio::sync::Mutex;

#[path = "support/friend_auth.rs"]
mod support;

const HUMAN: &str = "human_88123";
const BOT: &str = "abc123:85020";

#[derive(Default)]
struct RecordingSync {
    commands: Mutex<Vec<FriendAuthSyncCommand>>,
}

#[async_trait]
impl FriendAuthSyncPort for RecordingSync {
    async fn sync(&self, command: FriendAuthSyncCommand) -> ServiceResult<()> {
        self.commands.lock().await.push(command);
        Ok(())
    }
}

fn auth(principal: &str) -> RequestAuthHeaders {
    RequestAuthHeaders {
        authorization: None,
        cookie: None,
        forwarded_headers: vec![
            ("x-avernet-principal".into(), principal.into()),
            ("x-request-id".into(), "trace-friend-sync".into()),
        ],
    }
}

async fn fixture(strategy: &str) -> (DbConnectService, Arc<RecordingSync>) {
    let (edges, profiles, requests, bots, db) = support::assemble().await;
    support::seed_bot(&db, BOT, strategy).await;
    let sync = Arc::new(RecordingSync::default());
    let service = DbConnectService::new(
        edges, profiles, requests, bots, None,
        Arc::new(NoopFriendConnectNotificationPort), sync.clone(), "dev".into(),
    );
    (service, sync)
}

fn assert_command(
    command: &FriendAuthSyncCommand,
    action: FriendAuthSyncAction,
    request_id: Option<&str>,
    principal: &str,
) {
    assert_eq!(command.env, "dev");
    assert_eq!(command.bot_id, "abc123:85020", "keep the actor ID until the TC adapter");
    assert_eq!(command.human_work_no, "88123");
    assert_eq!(command.action, action);
    assert_eq!(command.request_id.as_deref(), request_id);
    assert_eq!(command.request_auth, Some(auth(principal)));
}

#[tokio::test]
async fn auto_approved_tc_friend_emits_grant_with_applicant_identity() {
    let (service, sync) = fixture("OPEN").await;
    let result = service.create_connect(HUMAN, BOT, None, Some(auth("applicant")))
        .await.expect("auto connect");
    assert_eq!(result.status, ConnectStatus::Approved);
    assert!(result.auto_accepted);
    assert_eq!(result.edge_ids.len(), 1);
    assert_eq!(result.request_ids.len(), 1);
    assert_eq!(service.list_friends(HUMAN).await.unwrap()[0].actor_id, BOT);
    let commands = sync.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_command(&commands[0], FriendAuthSyncAction::Grant, Some(&result.request_ids[0]), "applicant");
}

#[tokio::test]
async fn manual_tc_approval_emits_grant_only_after_approval_with_decider_identity() {
    let (service, sync) = fixture("APPROVAL").await;
    let pending = service.create_connect(HUMAN, BOT, None, Some(auth("applicant")))
        .await.expect("pending connect");
    assert_eq!(pending.status, ConnectStatus::Pending);
    assert!(pending.edge_ids.is_empty());
    assert_eq!(pending.request_ids.len(), 1);
    assert!(sync.commands.lock().await.is_empty(), "pending is not authorized yet");
    assert!(service.list_friends(HUMAN).await.unwrap().is_empty());

    let request_id = &pending.request_ids[0];
    let edges = service.approve(request_id, "85020", Some(auth("owner-decider")))
        .await.expect("approve");
    assert_eq!(edges.len(), 1);
    assert_eq!(service.list_friends(HUMAN).await.unwrap()[0].actor_id, BOT);
    let commands = sync.commands.lock().await;
    assert_eq!(commands.len(), 1);
    // The beneficiary remains the applicant, NOT the owner who approved.
    assert_command(&commands[0], FriendAuthSyncAction::Grant, Some(request_id), "owner-decider");
}

#[tokio::test]
async fn revoking_tc_friend_emits_revoke_with_current_caller_identity() {
    let (service, sync) = fixture("OPEN").await;
    let created = service.create_connect(HUMAN, BOT, None, Some(auth("applicant")))
        .await.expect("create friendship");
    assert_eq!(created.status, ConnectStatus::Approved);
    assert_eq!(created.edge_ids.len(), 1);
    assert_eq!(service.list_friends(HUMAN).await.unwrap()[0].actor_id, BOT);
    {
        let commands = sync.commands.lock().await;
        assert_eq!(commands.len(), 1);
        assert_command(&commands[0], FriendAuthSyncAction::Grant, Some(&created.request_ids[0]), "applicant");
    }

    let revoked = service.revoke_friend(HUMAN, BOT, Some(auth("unfriend-caller")))
        .await.expect("revoke friendship");
    assert_eq!(revoked, created.edge_ids);
    assert!(service.list_friends(HUMAN).await.unwrap().is_empty());
    let commands = sync.commands.lock().await;
    assert_eq!(commands.len(), 2, "one grant followed by one revoke");
    assert_command(&commands[1], FriendAuthSyncAction::Revoke, None, "unfriend-caller");
}
