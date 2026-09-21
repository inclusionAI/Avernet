use super::*;

use bcs_bot::BotCore;
use bcs_domain::{MessagePage, NewMessage, PersistedMessage, SenderType, SystemMessageEvent};
use bcs_group::GroupCore;
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{
    BotActor, CallerContext, Group, HumanActor, MessageRole, Participant, ParticipantRole,
    PendingGroupMessage, PendingGroupMessagePort, SessionKind,
    application::session_files::{
        CapabilitiesView, DeleteFileCommand, DownloadRoute, PrepareUploadCommand,
        PrepareUploadResult, SessionFileService, SessionFileUseCaseError, ShareConsumeResult,
        ShareMintCommand, ShareMintResult,
    },
    port::repo::{
        MessageRepoError, NewSessionParams, SessionFileListPage, SessionFileListParams,
        SessionRepoPort,
    },
};
use bcs_session_store::MemorySessionRepo;
use bcs_storage_api::ByteStream;
use tokio::sync::Mutex;

struct NoopPendingMessages;

struct StaticPendingMessages(Vec<PendingGroupMessage>);

#[async_trait]
impl PendingGroupMessagePort for NoopPendingMessages {
    async fn list_pending(
        &self,
        _group_id: &str,
        _session_id: Option<&str>,
    ) -> Vec<PendingGroupMessage> {
        Vec::new()
    }
}

#[async_trait]
impl PendingGroupMessagePort for StaticPendingMessages {
    async fn list_pending(
        &self,
        _group_id: &str,
        _session_id: Option<&str>,
    ) -> Vec<PendingGroupMessage> {
        self.0.clone()
    }
}

// Minimal mock: everything errors except share_mint_for_history (configurable).
struct MintMock {
    ok: bool,
}

#[async_trait]
impl SessionFileService for MintMock {
    async fn capabilities(&self) -> CapabilitiesView {
        unimplemented!()
    }
    async fn prepare_upload(
        &self,
        _cmd: PrepareUploadCommand,
    ) -> Result<PrepareUploadResult, SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn stream_upload(
        &self,
        _session_id: &str,
        _file_id: &str,
        _part_number: Option<u16>,
        _body: ByteStream,
        _content_length: u64,
    ) -> Result<(), SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn complete_upload(
        &self,
        _session_id: &str,
        _file_id: &str,
    ) -> Result<bcs_domain::SessionFile, SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn delete_file(
        &self,
        _cmd: DeleteFileCommand,
    ) -> Result<(), SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn get(
        &self,
        _session_id: &str,
        _file_id: &str,
    ) -> Result<bcs_domain::SessionFile, SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn list(
        &self,
        _session_id: &str,
        _params: SessionFileListParams,
    ) -> Result<SessionFileListPage, SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn download_route(
        &self,
        _session_id: &str,
        _file_id: &str,
        _ttl_secs: Option<u64>,
        _show: bool,
    ) -> Result<(bcs_domain::SessionFile, DownloadRoute), SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn share_mint(
        &self,
        _cmd: ShareMintCommand,
    ) -> Result<ShareMintResult, SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn share_consume(
        &self,
        _token: &str,
    ) -> Result<ShareConsumeResult, SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn get_stream(
        &self,
        _session_id: &str,
        _file_id: &str,
    ) -> Result<(bcs_domain::SessionFile, ByteStream), SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn sweep_expired_pending(&self) -> Result<u64, SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn delete_all_for_session(
        &self,
        _session_id: &str,
    ) -> Result<u64, SessionFileUseCaseError> {
        unimplemented!()
    }
    async fn share_mint_for_history(
        &self,
        _session_id: &str,
        _file_id: &str,
        _ttl_seconds: u64,
    ) -> Result<ShareMintResult, SessionFileUseCaseError> {
        if self.ok {
            Ok(ShareMintResult {
                share_url: "https://bcs/sessions/shared-file/content?token=x".into(),
                share_token: "x".into(),
                expires_at: 9999,
            })
        } else {
            Err(SessionFileUseCaseError::NotFound("nope".into()))
        }
    }
}

fn att_msg() -> GroupMessage {
    GroupMessage {
        id: "m".into(),
        timestamp: 1,
        sender: "s".into(),
        content: "t".into(),
        message_type: GroupMessageType::Bot,
        bot_name: None,
        role: MessageRole::User,
        run_id: String::new(),
        history_meta: None,
        metadata: None,
        attachments: Some(vec![MessageAttachment {
            attachment_id: "file_1".into(),
            attachment_type: bcs_domain::AttachmentType::Image,
            file_name: "f.png".into(),
            mime_type: None,
            size: None,
            sha256: None,
            url: None,
            expires_at: None,
        }]),
    }
}

#[tokio::test]
async fn enrich_fills_url_on_success() {
    let svc: Arc<dyn SessionFileService> = Arc::new(MintMock { ok: true });
    let mut msg = att_msg();
    enrich_message_attachments(&svc, "sid", 3600, &mut msg).await;
    let att = &msg.attachments.as_ref().unwrap()[0];
    assert_eq!(
        att.url.as_deref(),
        Some("https://bcs/sessions/shared-file/content?token=x")
    );
    assert_eq!(att.expires_at, Some(9999));
}

#[tokio::test]
async fn enrich_leaves_url_none_on_failure() {
    let svc: Arc<dyn SessionFileService> = Arc::new(MintMock { ok: false });
    let mut msg = att_msg();
    enrich_message_attachments(&svc, "sid", 3600, &mut msg).await;
    let att = &msg.attachments.as_ref().unwrap()[0];
    assert!(att.url.is_none());
    assert!(att.expires_at.is_none());
}

#[tokio::test]
async fn enrich_no_op_when_no_attachments() {
    let svc: Arc<dyn SessionFileService> = Arc::new(MintMock { ok: true });
    let mut msg = att_msg();
    msg.attachments = None;
    enrich_message_attachments(&svc, "sid", 3600, &mut msg).await;
    assert!(msg.attachments.is_none());
}

struct FallbackHistory {
    messages: Mutex<Vec<GroupMessage>>,
    group_calls: Mutex<usize>,
    session_calls: Mutex<usize>,
}

struct CountingMessageRepo {
    inner: Arc<MemoryMessageRepo>,
    query_calls: Mutex<usize>,
}

impl CountingMessageRepo {
    fn new(inner: Arc<MemoryMessageRepo>) -> Self {
        Self {
            inner,
            query_calls: Mutex::new(0),
        }
    }

    async fn query_calls(&self) -> usize {
        *self.query_calls.lock().await
    }
}

#[async_trait]
impl MessageRepoPort for CountingMessageRepo {
    async fn append_message(
        &self,
        msg: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        self.inner.append_message(msg).await
    }

    async fn query_messages(
        &self,
        query: MessageQuery,
    ) -> Result<MessagePage, MessageRepoError> {
        *self.query_calls.lock().await += 1;
        self.inner.query_messages(query).await
    }

    async fn get_message_by_id(
        &self,
        session_id: &str,
        message_id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError> {
        self.inner.get_message_by_id(session_id, message_id).await
    }

    async fn resolve_history_window_start(&self, session: &str, anchor: i64, limit: u64) -> Result<i64, MessageRepoError> { self.inner.resolve_history_window_start(session, anchor, limit).await }

    async fn get_current_seq(&self, session_id: &str) -> Result<i64, MessageRepoError> {
        self.inner.get_current_seq(session_id).await
    }
}

impl FallbackHistory {
    fn new(messages: Vec<GroupMessage>) -> Self {
        Self {
            messages: Mutex::new(messages),
            group_calls: Mutex::new(0),
            session_calls: Mutex::new(0),
        }
    }

    async fn group_calls(&self) -> usize {
        *self.group_calls.lock().await
    }

    async fn session_calls(&self) -> usize {
        *self.session_calls.lock().await
    }
}

#[async_trait]
impl GroupMessageHistoryService for FallbackHistory {
    async fn get_history(
        &self,
        cmd: GroupHistoryCommand,
    ) -> Result<GroupHistoryResult, GroupUseCaseError> {
        *self.group_calls.lock().await += 1;
        Ok(GroupHistoryResult {
            group_id: cmd.group_id,
            messages: self.messages.lock().await.clone(),
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }

    async fn get_session_history(
        &self,
        cmd: SessionHistoryCommand,
    ) -> Result<SessionHistoryResult, GroupUseCaseError> {
        *self.session_calls.lock().await += 1;
        Ok(SessionHistoryResult {
            session_id: cmd.session_id,
            messages: self.messages.lock().await.clone(),
            limit: cmd.limit,
            before: cmd.before,
            next_before: None,
        })
    }
}

fn fallback_message(content: &str) -> GroupMessage {
    GroupMessage {
        id: "fallback-msg".to_string(),
        timestamp: 1,
        sender: "legacy-bot".to_string(),
        content: content.to_string(),
        message_type: GroupMessageType::Bot,
        bot_name: None,
        role: MessageRole::Assistant,
        run_id: String::new(),
        history_meta: None,
        metadata: None,
        attachments: None,
    }
}

fn session_cmd(
    group_id: &str,
    session_id: &str,
    view_bot_id: Option<&str>,
) -> SessionHistoryCommand {
    SessionHistoryCommand {
        caller: CallerContext::Public,
        group_id: group_id.to_string(),
        session_id: session_id.to_string(),
        session_participants: Vec::new(),
        view_bot_id: view_bot_id.map(str::to_string),
        limit: 50,
        before: None,
    }
}

fn group_cmd(group_id: &str, view_bot_id: Option<&str>) -> GroupHistoryCommand {
    GroupHistoryCommand {
        caller: CallerContext::Public,
        group_id: group_id.to_string(),
        view_bot_id: view_bot_id.map(str::to_string),
        limit: 50,
        before: None,
    }
}

async fn service_fixture(
    strategy: GroupStrategy,
    chat_cutoff: u64,
    manager_worker_cutoff: u64,
    fallback_messages: Vec<GroupMessage>,
) -> (
    MessageService,
    Arc<MemoryMessageRepo>,
    Arc<MemorySessionRepo>,
    Arc<FallbackHistory>,
    String,
) {
    let group_id = "group-1".to_string();
    let session_id = "group-1:abcdef12".to_string();
    let group = Arc::new(GroupCore::memory());
    let session_repo = Arc::new(MemorySessionRepo::new());
    let message_repo = Arc::new(MemoryMessageRepo::new());
    let fallback = Arc::new(FallbackHistory::new(fallback_messages));

    let mut domain_group = Group::new(
        group_id.clone(),
        "mgr",
        vec![
            Participant::bot("mgr", ParticipantRole::Manager),
            Participant::bot("worker-a", ParticipantRole::Worker),
            Participant::bot("worker-b", ParticipantRole::Worker),
        ],
    );
    domain_group.group_strategy = strategy;
    group.upsert(domain_group).await.expect("upsert group");
    session_repo
        .create(
            &group_id,
            NewSessionParams {
                id: Some(session_id.clone()),
                session_kind: SessionKind::Chat,
                participants: Vec::new(),
                ..Default::default()
            },
        )
        .await
        .expect("create session");

    let service = MessageService::new(
        message_repo.clone(),
        fallback.clone(),
        session_repo.clone(),
        group,
        Arc::new(BotCore::memory()),
        Arc::new(MintMock { ok: true }),
        Arc::new(NoopPendingMessages),
        chat_cutoff,
        manager_worker_cutoff,
        100,
        50,
        100,
        3600,
    );

    (service, message_repo, session_repo, fallback, session_id)
}

async fn append_history(
    repo: &MemoryMessageRepo,
    group_id: &str,
    session_id: &str,
    sender_id: &str,
    content: &str,
    owner_bot_id: Option<&str>,
) {
    repo.append_message(NewMessage {
        group_id: group_id.to_string(),
        session_id: session_id.to_string(),
        sender_id: sender_id.to_string(),
        sender_type: SenderType::Bot,
        message_type: "chat".to_string(),
        content: serde_json::Value::String(content.to_string()),
        client_msg_id: None,
        created_at: 1,
        run_id: String::new(),
        owner_bot_id: owner_bot_id.map(str::to_string),
        visibility_domain: if owner_bot_id.is_some() {
            bcs_domain::MessageVisibilityDomain::ManagerWorker
        } else {
            bcs_domain::MessageVisibilityDomain::Chat
        },
        audience: Some(match owner_bot_id {
            Some(owner) => bcs_domain::MessageAudience::Directed {
                actor_ids: vec![owner.to_string()],
            },
            None => bcs_domain::MessageAudience::Public,
        }),
    })
    .await
    .expect("append history");
}

fn group_fixture(group_id: &str, driver_bot_id: &str) -> Group {
    let mut group = Group::new(
        group_id.to_string(),
        driver_bot_id,
        vec![
            Participant::bot(driver_bot_id, ParticipantRole::Manager),
            Participant::bot("worker-a", ParticipantRole::Worker),
            Participant::bot("worker-b", ParticipantRole::Worker),
        ],
    );
    group.group_strategy = GroupStrategy::Chat;
    group
}

mod attachment_parse_tests {
    use super::*;
    use bcs_domain::{PersistedMessage, SenderType};

    fn pm(content: serde_json::Value) -> PersistedMessage {
        PersistedMessage {
            message_id: "m".into(),
            group_id: "g".into(),
            session_id: "sid".into(),
            session_seq: 1,
            sender_id: "human_x".into(),
            sender_type: SenderType::Human,
            message_type: "chat".into(),
            content,
            client_msg_id: None,
            owner_bot_id: None,
            status: bcs_domain::PersistedMessageStatus::Normal,
            created_at: 1,
            run_id: String::new(),
            visibility_domain: None,
            audience: None,
        }
    }

    #[test]
    fn plain_string_content_returns_text_no_attachments() {
        let (text, atts) = extract_text_and_attachments(&serde_json::json!("hello"));
        assert_eq!(text, "hello");
        assert!(atts.is_none());
    }

    #[test]
    fn object_content_extracts_text_and_attachments_without_url() {
        let content = serde_json::json!({
            "text": "描述一下图片",
            "attachments": [{
                "attachment_id": "01KZ977A05N0TVGX8BKFA26T6D",
                "type": "image",
                "file_name": "109951168084935137.jpg",
                "mime_type": "image/jpeg",
                "sha256": null,
                "size": 13276
            }]
        });
        let (text, atts) = extract_text_and_attachments(&content);
        assert_eq!(text, "描述一下图片");
        let atts = atts.expect("attachments present");
        assert_eq!(atts.len(), 1);
        assert_eq!(atts[0].attachment_id, "01KZ977A05N0TVGX8BKFA26T6D");
        assert_eq!(atts[0].file_name, "109951168084935137.jpg");
        assert_eq!(atts[0].mime_type.as_deref(), Some("image/jpeg"));
        assert_eq!(atts[0].size, Some(13276));
        assert!(atts[0].url.is_none(), "url must be None at parse stage");
        assert!(atts[0].expires_at.is_none());
    }

    #[test]
    fn object_without_attachments_returns_text_only() {
        let content = serde_json::json!({"text": "only text"});
        let (text, atts) = extract_text_and_attachments(&content);
        assert_eq!(text, "only text");
        assert!(atts.is_none());
    }

    #[test]
    fn object_without_text_returns_empty_text() {
        let content = serde_json::json!({"attachments": [{"attachment_id":"a","type":"image","file_name":"f"}]});
        let (text, atts) = extract_text_and_attachments(&content);
        assert_eq!(text, "");
        assert!(atts.is_some());
    }

    #[test]
    fn attachment_missing_required_fields_is_dropped() {
        let content = serde_json::json!({
            "text": "t",
            "attachments": [
                {"type":"image","file_name":"no_id"},            // missing attachment_id -> dropped
                {"attachment_id":"a","file_name":"f"}            // missing type -> defaults to Image, kept
            ]
        });
        let (_t, atts) = extract_text_and_attachments(&content);
        let atts = atts.expect("some kept");
        assert_eq!(atts.len(), 1, "only the second attachment survives");
        assert_eq!(atts[0].attachment_id, "a");
    }

    #[test]
    fn persisted_to_group_message_preserves_text_and_attachments() {
        let content = serde_json::json!({
            "text": "描述一下图片",
            "attachments": [{"attachment_id":"a","type":"image","file_name":"f","mime_type":"image/png","size":4,"sha256":null}]
        });
        let gm = persisted_to_group_message(pm(content), None);
        assert_eq!(gm.content, "描述一下图片");
        let atts = gm.attachments.expect("attachments");
        assert_eq!(atts[0].attachment_id, "a");
        assert!(atts[0].url.is_none());
    }

    #[test]
    fn persisted_chat_error_is_assistant_text_with_terminal_metadata() {
        let mut row = pm(serde_json::json!("回复失败"));
        row.message_type = bcs_domain::CHAT_ERROR_MESSAGE_TYPE.into();
        let message = persisted_to_group_message(row, None);
        assert_eq!(message.role, MessageRole::Assistant);
        assert_eq!(message.content, "回复失败");
        assert_eq!(message.metadata.unwrap()["terminal_state"], "error");
        assert!(message.attachments.is_none());
    }
}

mod history;
mod fallback;
mod visibility;
mod loop_output;
