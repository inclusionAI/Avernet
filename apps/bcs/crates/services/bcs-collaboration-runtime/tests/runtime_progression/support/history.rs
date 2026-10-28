use super::super::{
    MemoryMessageRepo,
    MessagePage,
    MessageQuery,
    MessageRepoError,
    MessageRepoPort,
    NewMessage,
    PersistedMessage,
    async_trait
};

#[derive(Default)]
pub(in super::super) struct FailingAppendMessageRepo {
    inner: MemoryMessageRepo,
}

#[async_trait]
impl MessageRepoPort for FailingAppendMessageRepo {
    async fn append_message_with_id(&self, _id: String, message: NewMessage) -> Result<PersistedMessage, MessageRepoError> {
        self.append_message(message).await
    }

    async fn append_message(
        &self,
        _message: NewMessage,
    ) -> Result<PersistedMessage, MessageRepoError> {
        Err(MessageRepoError::StorageError(
            "opening message write failed".to_string(),
        ))
    }

    async fn query_messages(&self, query: MessageQuery) -> Result<MessagePage, MessageRepoError> {
        self.inner.query_messages(query).await
    }

    async fn get_message_by_id(
        &self,
        session_id: &str,
        message_id: &str,
    ) -> Result<Option<PersistedMessage>, MessageRepoError> {
        self.inner.get_message_by_id(session_id, message_id).await
    }

    async fn get_current_seq(&self, session_id: &str) -> Result<i64, MessageRepoError> {
        self.inner.get_current_seq(session_id).await
    }
}
