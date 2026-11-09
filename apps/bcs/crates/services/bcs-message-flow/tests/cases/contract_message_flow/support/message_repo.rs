use super::*;

#[derive(Default)]
pub(super) struct RecordingMessageRepo {
    appended: RwLock<Vec<bcs_domain::NewMessage>>,
    events: RwLock<Vec<bcs_service_api::port::repo::AppendEventRecord>>,
}

impl RecordingMessageRepo {
    pub(super) async fn appended(&self) -> Vec<bcs_domain::NewMessage> {
        self.appended.read().await.clone()
    }

    pub(super) async fn events(&self) -> Vec<bcs_service_api::port::repo::AppendEventRecord> {
        self.events.read().await.clone()
    }
}

#[async_trait::async_trait]
impl bcs_service_api::port::repo::MessageRepoPort for RecordingMessageRepo {
    async fn append_message(
        &self,
        msg: bcs_domain::NewMessage,
    ) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> {
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

    async fn append_message_with_event(
        &self,
        command: bcs_service_api::port::repo::AppendMessageWithEvent,
    ) -> Result<bcs_domain::PersistedMessage, bcs_service_api::port::repo::MessageRepoError> {
        self.events.write().await.push(command.event);
        self.append_message(command.message).await
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
