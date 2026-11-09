use super::super::{
    AtomicBool,
    Barrier,
    Duration,
    Notify,
    Ordering,
    RwLock,
    timeout
};

// -- Chat persistence on terminal error/abort --

/// Minimal `MessageRepoPort` that records every appended message so tests can
/// assert what got persisted to history.
#[derive(Default)]
pub(in super::super) struct RecordingMessageRepo {
    appended: RwLock<Vec<bcs_domain::NewMessage>>,
    fail_appends: AtomicBool,
}

impl RecordingMessageRepo {
    pub(in super::super) async fn appended(&self) -> Vec<bcs_domain::NewMessage> {
        self.appended.read().await.clone()
    }

    pub(in super::super) fn set_fail_appends(&self, fail: bool) {
        self.fail_appends.store(fail, Ordering::Relaxed);
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::repo::MessageRepoPort for RecordingMessageRepo {
    async fn run_chat_segments(&self, session: &str, sender: &str, run: &str) -> Result<Vec<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError> {
        let memory = bcs_message_store::MemoryMessageRepo::new();
        for message in self.appended.read().await.iter().cloned() { memory.append_message(message).await?; }
        memory.run_chat_segments(session, sender, run).await
    }
    async fn append_message(
        &self,
        msg: bcs_domain::NewMessage,
    ) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> {
        if self.fail_appends.load(Ordering::Relaxed) {
            return Err(bcs_service_api::port::repo::MessageRepoError::StorageError(
                "injected append failure".to_string(),
            ));
        }
        let seq = self.appended.read().await.len() as i64 + 1;
        let persisted = bcs_domain::PersistedMessage {
            message_id: format!("msg-{seq}"),
            group_id: msg.group_id.clone(),
            session_id: msg.session_id.clone(),
            session_seq: seq,
            sender_id: msg.sender_id.clone(),
            sender_type: msg.sender_type,
            message_type: msg.message_type.clone(),
            content: msg.content.clone(),
            client_msg_id: msg.client_msg_id.clone(),
            owner_bot_id: msg.owner_bot_id.clone(),
            status: bcs_domain::PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id.clone(),
            visibility_domain: Some(msg.visibility_domain),
            audience: msg.audience.clone(),
        };
        self.appended.write().await.push(msg);
        Ok(persisted)
    }

    async fn query_messages(
        &self,
        _query: bcs_domain::MessageQuery,
    ) -> Result<bcs_domain::MessagePage, bcs_service_api::port::repo::MessageRepoError> {
        Ok(bcs_domain::MessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }

    async fn get_message_by_id(
        &self,
        _session_id: &str,
        _message_id: &str,
    ) -> Result<Option<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError>
    {
        Ok(None)
    }

    async fn get_current_seq(
        &self,
        _session_id: &str,
    ) -> Result<i64, bcs_service_api::port::repo::MessageRepoError> {
        Ok(self.appended.read().await.len() as i64)
    }
}

pub(in super::super) struct BlockingMessageRepo {
    appended: RwLock<Vec<bcs_domain::NewMessage>>,
    append_reached: Barrier,
    release_append: Notify,
}

impl BlockingMessageRepo {
    pub(in super::super) fn new() -> Self {
        Self {
            appended: RwLock::new(Vec::new()),
            append_reached: Barrier::new(2),
            release_append: Notify::new(),
        }
    }

    pub(in super::super) async fn wait_for_append(&self) {
        timeout(Duration::from_secs(1), self.append_reached.wait())
            .await
            .expect("message append should start");
    }

    pub(in super::super) fn release_append(&self) {
        self.release_append.notify_one();
    }

    pub(in super::super) async fn appended(&self) -> Vec<bcs_domain::NewMessage> {
        self.appended.read().await.clone()
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::repo::MessageRepoPort for BlockingMessageRepo {
    async fn run_chat_segments(&self, session: &str, sender: &str, run: &str) -> Result<Vec<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError> {
        let memory = bcs_message_store::MemoryMessageRepo::new();
        for message in self.appended.read().await.iter().cloned() { memory.append_message(message).await?; }
        memory.run_chat_segments(session, sender, run).await
    }
    async fn append_message(
        &self,
        msg: bcs_domain::NewMessage,
    ) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> {
        self.append_reached.wait().await;
        self.release_append.notified().await;

        let seq = self.appended.read().await.len() as i64 + 1;
        let persisted = bcs_domain::PersistedMessage {
            message_id: format!("msg-{seq}"),
            group_id: msg.group_id.clone(),
            session_id: msg.session_id.clone(),
            session_seq: seq,
            sender_id: msg.sender_id.clone(),
            sender_type: msg.sender_type,
            message_type: msg.message_type.clone(),
            content: msg.content.clone(),
            client_msg_id: msg.client_msg_id.clone(),
            owner_bot_id: msg.owner_bot_id.clone(),
            status: bcs_domain::PersistedMessageStatus::Normal,
            created_at: msg.created_at,
            run_id: msg.run_id.clone(),
            visibility_domain: Some(msg.visibility_domain),
            audience: msg.audience.clone(),
        };
        self.appended.write().await.push(msg);
        Ok(persisted)
    }

    async fn query_messages(
        &self,
        _query: bcs_domain::MessageQuery,
    ) -> Result<bcs_domain::MessagePage, bcs_service_api::port::repo::MessageRepoError> {
        Ok(bcs_domain::MessagePage {
            messages: Vec::new(),
            next_cursor: None,
            has_more: false,
        })
    }

    async fn get_message_by_id(
        &self,
        _session_id: &str,
        _message_id: &str,
    ) -> Result<Option<bcs_domain::PersistedMessage>, bcs_service_api::port::repo::MessageRepoError>
    {
        Ok(None)
    }

    async fn get_current_seq(
        &self,
        _session_id: &str,
    ) -> Result<i64, bcs_service_api::port::repo::MessageRepoError> {
        Ok(self.appended.read().await.len() as i64)
    }
}
