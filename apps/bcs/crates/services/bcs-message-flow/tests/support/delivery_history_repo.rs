//! Records the additional bounded history-window read without mocking its result.
use std::sync::{Arc, atomic::{AtomicBool, Ordering}, Mutex};
use bcs_domain::{MessagePage, MessageQuery, NewMessage, PersistedMessage};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::port::repo::{MessageRepoError, MessageRepoPort};

pub struct WindowProbe {
    pub inner: Arc<MemoryMessageRepo>,
    pub calls: Mutex<Vec<(i64, u64)>>,
    pub fail: AtomicBool,
}

impl WindowProbe {
    pub fn new(inner: Arc<MemoryMessageRepo>) -> Self {
        Self { inner, calls: Mutex::new(Vec::new()), fail: AtomicBool::new(false) }
    }
}

#[async_trait::async_trait]
impl MessageRepoPort for WindowProbe {
    async fn resolve_history_window_start(&self, session: &str, anchor: i64, limit: u64) -> Result<i64, MessageRepoError> {
        self.calls.lock().unwrap().push((anchor, limit));
        if self.fail.load(Ordering::SeqCst) {
            return Err(MessageRepoError::StorageError("injected window read failure".into()));
        }
        self.inner.resolve_history_window_start(session, anchor, limit).await
    }

    async fn append_message(&self, message: NewMessage) -> Result<PersistedMessage, MessageRepoError> {
        self.inner.append_message(message).await
    }

    async fn query_messages(&self, query: MessageQuery) -> Result<MessagePage, MessageRepoError> {
        self.inner.query_messages(query).await
    }

    async fn get_message_by_id(&self, session: &str, message: &str) -> Result<Option<PersistedMessage>, MessageRepoError> {
        self.inner.get_message_by_id(session, message).await
    }

    async fn get_current_seq(&self, session: &str) -> Result<i64, MessageRepoError> {
        self.inner.get_current_seq(session).await
    }
}
