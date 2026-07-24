//! DB-backed channel repository implementations.
//!
//! Required tables (DDL is executed by deployment/migration tooling):
//!
//! ```sql
//! CREATE TABLE bcs_channel_bindings (
//!   id               VARCHAR(64) PRIMARY KEY,
//!   gmt_create       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
//!   gmt_modified     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
//!   channel_type     VARCHAR(32) NOT NULL,
//!   account_ref      VARCHAR(128) NOT NULL,
//!   target_json      TEXT NOT NULL,
//!   group_chat_scope VARCHAR(32) DEFAULT NULL,
//!   visibility       VARCHAR(32) NOT NULL,
//!   env              VARCHAR(32) NOT NULL,
//!   status           VARCHAR(16) NOT NULL,
//!   created_by       VARCHAR(256) DEFAULT NULL,
//!   config_json      TEXT NOT NULL,
//!   INDEX idx_channel_bindings_account (channel_type, account_ref, status)
//! );
//!
//! CREATE TABLE bcs_channel_conversations (
//!   binding_id           VARCHAR(64) NOT NULL,
//!   gmt_create           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
//!   gmt_modified         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
//!   im_conversation_id   VARCHAR(256) NOT NULL,
//!   im_conversation_type VARCHAR(16) NOT NULL,
//!   session_scope        VARCHAR(32) NOT NULL,
//!   im_user_id           VARCHAR(128) NOT NULL DEFAULT '',
//!   bcs_session_id       VARCHAR(128) NOT NULL,
//!   last_active_at       BIGINT NOT NULL,
//!   PRIMARY KEY (binding_id, im_conversation_id, session_scope, im_user_id),
//!   INDEX idx_channel_conversations_session (binding_id, bcs_session_id),
//!   INDEX idx_channel_conversations_bcs_session (bcs_session_id, binding_id)
//! );
//!
//! CREATE TABLE bcs_channel_im_participants (
//!   channel_type VARCHAR(32) NOT NULL,
//!   gmt_create   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
//!   gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
//!   account_ref  VARCHAR(128) NOT NULL,
//!   im_user_id   VARCHAR(128) NOT NULL,
//!   actor_id     VARCHAR(256) NOT NULL,
//!   display_name VARCHAR(256) DEFAULT NULL,
//!   PRIMARY KEY (channel_type, account_ref, im_user_id)
//! );
//! ```
//!
//! The deployment side executes DDL; this module owns only SQL DML and
//! row-to-domain mapping for the channel repository ports.

use std::sync::Arc;

use async_trait::async_trait;
use tracing::warn;

use bcs_db_api::{DbError, DbPlugin, DbRow, DbSqlFlavor, DbStatement, DbValue};
use bcs_domain::{
    BindingStatus, BindingTarget, ChannelBinding, ChannelType, ConversationSessionMap,
    GroupChatScope, ImParticipantMap, SessionScope, Visibility,
};
use bcs_service_api::port::repo::{
    ChannelBindingRepoPort, ConversationSessionRepoPort, ImParticipantRepoPort,
};
use bcs_service_api::{ServiceError, ServiceResult};

pub type ChannelSqlFlavor = DbSqlFlavor;

pub struct DbChannelBindingStore {
    db: Arc<dyn DbPlugin>,
    flavor: ChannelSqlFlavor,
    env: String,
}

impl DbChannelBindingStore {
    pub fn new(
        db: Arc<dyn DbPlugin>,
        flavor: ChannelSqlFlavor,
        env: impl Into<String>,
    ) -> Self {
        Self {
            db,
            flavor,
            env: env.into(),
        }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>, env: impl Into<String>) -> Self {
        Self::new(db, ChannelSqlFlavor::Mysql, env)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>, env: impl Into<String>) -> Self {
        Self::new(db, ChannelSqlFlavor::Sqlite, env)
    }

    pub fn flavor(&self) -> ChannelSqlFlavor {
        self.flavor
    }

    async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<u64> {
        self.db
            .execute(statement)
            .await
            .map(|result| result.affected_rows)
            .map_err(|err| service_db_error(operation, err))
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db
            .query(statement)
            .await
            .map_err(|err| service_db_error(operation, err))
    }
}

#[async_trait]
impl ChannelBindingRepoPort for DbChannelBindingStore {
    async fn create(&self, binding: ChannelBinding) -> ServiceResult<()> {
        // 以下为安全注释COSEC：拒绝跨环境写入，避免调用方绕过 repository 的环境隔离。
        if binding.env != self.env {
            return Err(ServiceError::InternalError(format!(
                "channel binding env '{}' does not match repository env '{}'",
                binding.env, self.env
            )));
        }
        let target_json = serde_json::to_string(&binding.target)?;
        let config_json = serde_json::to_string(&binding.config)?;

        self.execute(
            "create_binding",
            DbStatement::with_params(
                "INSERT INTO bcs_channel_bindings \
                 (id, channel_type, account_ref, target_json, group_chat_scope, \
                  visibility, env, status, created_by, config_json) \
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                vec![
                    DbValue::from(binding.id.as_str()),
                    DbValue::from(binding.channel_type.as_str()),
                    DbValue::from(binding.account_ref.as_str()),
                    DbValue::from(target_json),
                    DbValue::from(binding.group_chat_scope.map(group_chat_scope_to_str)),
                    DbValue::from(visibility_to_str(binding.outbound_visibility)),
                    DbValue::from(binding.env.as_str()),
                    DbValue::from(binding_status_to_str(binding.status)),
                    DbValue::from(binding.created_by.as_deref()),
                    DbValue::from(config_json),
                ],
            ),
        )
        .await?;
        Ok(())
    }

    async fn get(&self, id: &str) -> ServiceResult<Option<ChannelBinding>> {
        let rows = self
            .query(
                "get_binding",
                DbStatement::with_params(
                    "SELECT id, channel_type, account_ref, target_json, group_chat_scope, \
                     visibility, env, status, created_by, config_json \
                     FROM bcs_channel_bindings WHERE id = ? AND env = ? LIMIT 1",
                    vec![DbValue::from(id), DbValue::from(self.env.as_str())],
                ),
            )
            .await?;

        match rows.first() {
            Some(row) => row_to_binding(row).map(Some),
            None => Ok(None),
        }
    }

    async fn find_active_by_account(
        &self,
        channel_type: ChannelType,
        account_ref: &str,
    ) -> ServiceResult<Option<ChannelBinding>> {
        let rows = self
            .query(
                "find_active_binding_by_account",
                DbStatement::with_params(
                    "SELECT id, channel_type, account_ref, target_json, group_chat_scope, \
                     visibility, env, status, created_by, config_json \
                     FROM bcs_channel_bindings \
                     WHERE env = ? AND channel_type = ? AND account_ref = ? \
                       AND status = 'active' \
                     LIMIT 1",
                    vec![
                        DbValue::from(self.env.as_str()),
                        DbValue::from(channel_type.as_str()),
                        DbValue::from(account_ref),
                    ],
                ),
            )
            .await?;

        match rows.first() {
            Some(row) => row_to_binding(row).map(Some),
            None => Ok(None),
        }
    }

    async fn list(&self) -> ServiceResult<Vec<ChannelBinding>> {
        let rows = self
            .query(
                "list_bindings",
                DbStatement::with_params(
                    "SELECT id, channel_type, account_ref, target_json, group_chat_scope, \
                     visibility, env, status, created_by, config_json \
                     FROM bcs_channel_bindings WHERE env = ? ORDER BY id",
                    vec![DbValue::from(self.env.as_str())],
                ),
            )
            .await?;
        rows.iter().map(row_to_binding).collect()
    }

    async fn list_by_target(
        &self,
        target: &BindingTarget,
        channel_type: Option<&str>,
    ) -> ServiceResult<Vec<ChannelBinding>> {
        let target_json = serde_json::to_string(target)?;
        let statement = match channel_type {
            Some(channel_type) => DbStatement::with_params(
                "SELECT id, channel_type, account_ref, target_json, group_chat_scope, \
                 visibility, env, status, created_by, config_json \
                 FROM bcs_channel_bindings \
                 WHERE env = ? AND target_json = ? AND channel_type = ? ORDER BY id",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(target_json),
                    DbValue::from(channel_type),
                ],
            ),
            None => DbStatement::with_params(
                "SELECT id, channel_type, account_ref, target_json, group_chat_scope, \
                 visibility, env, status, created_by, config_json \
                 FROM bcs_channel_bindings \
                 WHERE env = ? AND target_json = ? ORDER BY id",
                vec![
                    DbValue::from(self.env.as_str()),
                    DbValue::from(target_json),
                ],
            ),
        };
        let rows = self.query("list_bindings_by_target", statement).await?;
        rows.iter().map(row_to_binding).collect()
    }

    async fn delete_by_target(&self, target: &BindingTarget) -> ServiceResult<u64> {
        let target_json = serde_json::to_string(target)?;
        // 以下为安全注释COSEC：删除范围固定为 repository env，禁止调用方选择其他环境。
        self.execute(
            "delete_bindings_by_target",
            DbStatement::with_params(
                "DELETE FROM bcs_channel_bindings WHERE target_json = ? AND env = ?",
                vec![
                    DbValue::from(target_json),
                    DbValue::from(self.env.as_str()),
                ],
            ),
        )
        .await
    }

    async fn set_status(&self, id: &str, active: bool) -> ServiceResult<()> {
        let status = if active {
            BindingStatus::Active
        } else {
            BindingStatus::Disabled
        };
        self.execute(
            "set_binding_status",
            DbStatement::with_params(
                format!(
                    "UPDATE bcs_channel_bindings SET status = ?, {} WHERE id = ? AND env = ?",
                    self.flavor.set_modified_now()
                ),
                vec![
                    DbValue::from(binding_status_to_str(status)),
                    DbValue::from(id),
                    DbValue::from(self.env.as_str()),
                ],
            ),
        )
        .await?;
        Ok(())
    }

    async fn set_config(&self, id: &str, config: serde_json::Value) -> ServiceResult<()> {
        let config_json = serde_json::to_string(&config)?;
        self.execute(
            "set_binding_config",
            DbStatement::with_params(
                format!(
                    "UPDATE bcs_channel_bindings SET config_json = ?, {} WHERE id = ? AND env = ?",
                    self.flavor.set_modified_now()
                ),
                vec![
                    DbValue::from(config_json),
                    DbValue::from(id),
                    DbValue::from(self.env.as_str()),
                ],
            ),
        )
        .await?;
        Ok(())
    }

    async fn delete(&self, id: &str) -> ServiceResult<()> {
        self.execute(
            "delete_binding",
            DbStatement::with_params(
                "DELETE FROM bcs_channel_bindings WHERE id = ? AND env = ?",
                vec![DbValue::from(id), DbValue::from(self.env.as_str())],
            ),
        )
        .await?;
        Ok(())
    }
}

pub struct DbConversationSessionStore {
    db: Arc<dyn DbPlugin>,
    flavor: ChannelSqlFlavor,
}

impl DbConversationSessionStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: ChannelSqlFlavor) -> Self {
        Self { db, flavor }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, ChannelSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, ChannelSqlFlavor::Sqlite)
    }

    pub fn flavor(&self) -> ChannelSqlFlavor {
        self.flavor
    }

    async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<u64> {
        self.db
            .execute(statement)
            .await
            .map(|result| result.affected_rows)
            .map_err(|err| service_db_error(operation, err))
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db
            .query(statement)
            .await
            .map_err(|err| service_db_error(operation, err))
    }

    fn upsert_sql(&self) -> String {
        format!(
            "INSERT INTO bcs_channel_conversations \
             (binding_id, im_conversation_id, im_conversation_type, session_scope, \
              im_user_id, bcs_session_id, last_active_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?) {}",
            self.flavor.on_conflict_update(
                &[
                    "binding_id",
                    "im_conversation_id",
                    "session_scope",
                    "im_user_id",
                ],
                &["im_conversation_type", "bcs_session_id", "last_active_at"],
                &[("gmt_modified", self.flavor.now())],
            )
        )
    }
}

#[async_trait]
impl ConversationSessionRepoPort for DbConversationSessionStore {
    async fn get(
        &self,
        binding_id: &str,
        im_conversation_id: &str,
        session_scope: SessionScope,
        im_user_id: Option<&str>,
    ) -> ServiceResult<Option<ConversationSessionMap>> {
        let rows = self
            .query(
                "get_conversation",
                DbStatement::with_params(
                    "SELECT binding_id, im_conversation_id, im_conversation_type, \
                     session_scope, im_user_id, bcs_session_id, last_active_at \
                     FROM bcs_channel_conversations \
                     WHERE binding_id = ? AND im_conversation_id = ? \
                       AND session_scope = ? AND im_user_id = ? \
                     LIMIT 1",
                    vec![
                        DbValue::from(binding_id),
                        DbValue::from(im_conversation_id),
                        DbValue::from(session_scope_to_str(session_scope)),
                        DbValue::from(im_user_id_value(im_user_id)),
                    ],
                ),
            )
            .await?;

        match rows.first() {
            Some(row) => row_to_conversation(row).map(Some),
            None => Ok(None),
        }
    }

    async fn find_by_session(
        &self,
        binding_id: &str,
        bcs_session_id: &str,
    ) -> ServiceResult<Option<ConversationSessionMap>> {
        let rows = self
            .query(
                "find_conversation_by_session",
                DbStatement::with_params(
                    "SELECT binding_id, im_conversation_id, im_conversation_type, \
                     session_scope, im_user_id, bcs_session_id, last_active_at \
                     FROM bcs_channel_conversations \
                     WHERE binding_id = ? AND bcs_session_id = ? \
                     LIMIT 1",
                    vec![DbValue::from(binding_id), DbValue::from(bcs_session_id)],
                ),
            )
            .await?;

        match rows.first() {
            Some(row) => row_to_conversation(row).map(Some),
            None => Ok(None),
        }
    }

    async fn list_by_bcs_session(
        &self,
        bcs_session_id: &str,
    ) -> ServiceResult<Vec<ConversationSessionMap>> {
        let rows = self
            .query(
                "list_conversations_by_bcs_session",
                DbStatement::with_params(
                    "SELECT binding_id, im_conversation_id, im_conversation_type, \
                     session_scope, im_user_id, bcs_session_id, last_active_at \
                     FROM bcs_channel_conversations \
                     WHERE bcs_session_id = ? \
                     ORDER BY binding_id",
                    vec![DbValue::from(bcs_session_id)],
                ),
            )
            .await?;

        rows.iter().map(row_to_conversation).collect()
    }

    async fn upsert(&self, map: ConversationSessionMap) -> ServiceResult<()> {
        self.execute(
            "upsert_conversation",
            DbStatement::with_params(
                self.upsert_sql(),
                vec![
                    DbValue::from(map.binding_id.as_str()),
                    DbValue::from(map.im_conversation_id.as_str()),
                    DbValue::from(map.im_conversation_type.as_str()),
                    DbValue::from(session_scope_to_str(map.session_scope)),
                    DbValue::from(im_user_id_value(map.im_user_id.as_deref())),
                    DbValue::from(map.bcs_session_id.as_str()),
                    DbValue::from(map.last_active_at),
                ],
            ),
        )
        .await?;
        Ok(())
    }

    async fn delete_if_session(
        &self,
        binding_id: &str,
        im_conversation_id: &str,
        session_scope: SessionScope,
        im_user_id: Option<&str>,
        expected_bcs_session_id: &str,
    ) -> ServiceResult<bool> {
        let affected = self
            .execute(
                "delete_conversation_if_session",
                DbStatement::with_params(
                    "DELETE FROM bcs_channel_conversations \
                     WHERE binding_id = ? AND im_conversation_id = ? \
                       AND session_scope = ? AND im_user_id = ? \
                       AND bcs_session_id = ?",
                    vec![
                        DbValue::from(binding_id),
                        DbValue::from(im_conversation_id),
                        DbValue::from(session_scope_to_str(session_scope)),
                        DbValue::from(im_user_id_value(im_user_id)),
                        DbValue::from(expected_bcs_session_id),
                    ],
                ),
            )
            .await?;
        Ok(affected == 1)
    }
}

pub struct DbImParticipantStore {
    db: Arc<dyn DbPlugin>,
    flavor: ChannelSqlFlavor,
}

impl DbImParticipantStore {
    pub fn new(db: Arc<dyn DbPlugin>, flavor: ChannelSqlFlavor) -> Self {
        Self { db, flavor }
    }

    pub fn mysql(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, ChannelSqlFlavor::Mysql)
    }

    pub fn sqlite(db: Arc<dyn DbPlugin>) -> Self {
        Self::new(db, ChannelSqlFlavor::Sqlite)
    }

    pub fn flavor(&self) -> ChannelSqlFlavor {
        self.flavor
    }

    async fn execute(&self, operation: &'static str, statement: DbStatement) -> ServiceResult<u64> {
        self.db
            .execute(statement)
            .await
            .map(|result| result.affected_rows)
            .map_err(|err| service_db_error(operation, err))
    }

    async fn query(
        &self,
        operation: &'static str,
        statement: DbStatement,
    ) -> ServiceResult<Vec<DbRow>> {
        self.db
            .query(statement)
            .await
            .map_err(|err| service_db_error(operation, err))
    }

    fn upsert_sql(&self) -> String {
        format!(
            "INSERT INTO bcs_channel_im_participants \
             (channel_type, account_ref, im_user_id, actor_id, display_name) \
             VALUES (?, ?, ?, ?, ?) {}",
            self.flavor.on_conflict_update(
                &["channel_type", "account_ref", "im_user_id"],
                &["actor_id", "display_name"],
                &[("gmt_modified", self.flavor.now())],
            )
        )
    }
}

#[async_trait]
impl ImParticipantRepoPort for DbImParticipantStore {
    async fn get(
        &self,
        channel_type: ChannelType,
        account_ref: &str,
        im_user_id: &str,
    ) -> ServiceResult<Option<ImParticipantMap>> {
        let rows = self
            .query(
                "get_participant",
                DbStatement::with_params(
                    "SELECT channel_type, account_ref, im_user_id, actor_id, display_name \
                     FROM bcs_channel_im_participants \
                     WHERE channel_type = ? AND account_ref = ? AND im_user_id = ? \
                     LIMIT 1",
                    vec![
                        DbValue::from(channel_type.as_str()),
                        DbValue::from(account_ref),
                        DbValue::from(im_user_id),
                    ],
                ),
            )
            .await?;

        match rows.first() {
            Some(row) => row_to_participant(row).map(Some),
            None => Ok(None),
        }
    }

    async fn upsert(&self, map: ImParticipantMap) -> ServiceResult<()> {
        self.execute(
            "upsert_participant",
            DbStatement::with_params(
                self.upsert_sql(),
                vec![
                    DbValue::from(map.channel_type.as_str()),
                    DbValue::from(map.account_ref.as_str()),
                    DbValue::from(map.im_user_id.as_str()),
                    DbValue::from(map.actor_id.as_str()),
                    DbValue::from(map.display_name.as_deref()),
                ],
            ),
        )
        .await?;
        Ok(())
    }
}

fn row_to_binding(row: &DbRow) -> ServiceResult<ChannelBinding> {
    let target_json = required_string(row, "target_json")?;
    let config_json = required_string(row, "config_json")?;

    Ok(ChannelBinding {
        id: required_string(row, "id")?,
        channel_type: required_string(row, "channel_type")?,
        account_ref: required_string(row, "account_ref")?,
        target: serde_json::from_str::<BindingTarget>(&target_json)?,
        group_chat_scope: parse_group_chat_scope(
            optional_string(row, "group_chat_scope").as_deref(),
        )?,
        outbound_visibility: parse_visibility(&required_string(row, "visibility")?)?,
        env: required_string(row, "env")?,
        status: parse_binding_status(&required_string(row, "status")?)?,
        created_by: optional_string(row, "created_by"),
        config: serde_json::from_str::<serde_json::Value>(&config_json)?,
    })
}

fn row_to_conversation(row: &DbRow) -> ServiceResult<ConversationSessionMap> {
    let im_user_id = optional_string(row, "im_user_id");
    Ok(ConversationSessionMap {
        binding_id: required_string(row, "binding_id")?,
        im_conversation_id: required_string(row, "im_conversation_id")?,
        im_conversation_type: required_string(row, "im_conversation_type")?,
        session_scope: parse_session_scope(&required_string(row, "session_scope")?)?,
        im_user_id: match im_user_id.as_deref() {
            Some("") | None => None,
            Some(value) => Some(value.to_string()),
        },
        bcs_session_id: required_string(row, "bcs_session_id")?,
        last_active_at: row_u64(row, "last_active_at")?,
    })
}

fn row_to_participant(row: &DbRow) -> ServiceResult<ImParticipantMap> {
    Ok(ImParticipantMap {
        channel_type: required_string(row, "channel_type")?,
        account_ref: required_string(row, "account_ref")?,
        im_user_id: required_string(row, "im_user_id")?,
        actor_id: required_string(row, "actor_id")?,
        display_name: optional_string(row, "display_name"),
    })
}

fn required_string(row: &DbRow, column: &'static str) -> ServiceResult<String> {
    row.get_string(column)
        .map_err(|err| service_db_error(column, err))?
        .ok_or_else(|| ServiceError::InternalError(format!("missing channel column {}", column)))
}

fn optional_string(row: &DbRow, column: &'static str) -> Option<String> {
    row.get_string(column).ok().flatten()
}

fn row_u64(row: &DbRow, column: &'static str) -> ServiceResult<u64> {
    let value = row
        .get_i64(column)
        .map_err(|err| service_db_error(column, err))?
        .ok_or_else(|| ServiceError::InternalError(format!("missing channel column {}", column)))?;
    u64::try_from(value).map_err(|_| {
        ServiceError::InternalError(format!(
            "channel column {} must be non-negative, got {}",
            column, value
        ))
    })
}

fn im_user_id_value(im_user_id: Option<&str>) -> &str {
    im_user_id.unwrap_or_default()
}

fn group_chat_scope_to_str(scope: GroupChatScope) -> &'static str {
    match scope {
        GroupChatScope::ConversationShared => "conversation_shared",
        GroupChatScope::PerSender => "per_sender",
    }
}

fn parse_group_chat_scope(value: Option<&str>) -> ServiceResult<Option<GroupChatScope>> {
    match value {
        Some("conversation_shared") => Ok(Some(GroupChatScope::ConversationShared)),
        Some("per_sender") => Ok(Some(GroupChatScope::PerSender)),
        Some(other) => Err(ServiceError::InternalError(format!(
            "unknown group_chat_scope {}",
            other
        ))),
        None => Ok(None),
    }
}

fn session_scope_to_str(scope: SessionScope) -> &'static str {
    match scope {
        SessionScope::Conversation => "conversation",
        SessionScope::PerSender => "per_sender",
    }
}

fn parse_session_scope(value: &str) -> ServiceResult<SessionScope> {
    match value {
        "conversation" => Ok(SessionScope::Conversation),
        "per_sender" => Ok(SessionScope::PerSender),
        _ => Err(ServiceError::InternalError(format!(
            "unknown session_scope {}",
            value
        ))),
    }
}

fn visibility_to_str(visibility: Visibility) -> &'static str {
    match visibility {
        Visibility::FullTranscript => "full_transcript",
        Visibility::LeadOnly => "lead_only",
    }
}

fn parse_visibility(value: &str) -> ServiceResult<Visibility> {
    match value {
        "full_transcript" => Ok(Visibility::FullTranscript),
        "lead_only" => Ok(Visibility::LeadOnly),
        _ => Err(ServiceError::InternalError(format!(
            "unknown visibility {}",
            value
        ))),
    }
}

fn binding_status_to_str(status: BindingStatus) -> &'static str {
    match status {
        BindingStatus::Active => "active",
        BindingStatus::Disabled => "disabled",
    }
}

fn parse_binding_status(value: &str) -> ServiceResult<BindingStatus> {
    match value {
        "active" => Ok(BindingStatus::Active),
        "disabled" => Ok(BindingStatus::Disabled),
        _ => Err(ServiceError::InternalError(format!(
            "unknown binding status {}",
            value
        ))),
    }
}

fn service_db_error(operation: &'static str, err: DbError) -> ServiceError {
    warn!(operation, error = %err, "channel db operation failed");
    ServiceError::InternalError(format!("channel db {}: {}", operation, err))
}

#[cfg(test)]
mod tests {
    use super::*;

    use bcs_db_api::{DbError, DbStatement};
    use bcs_db_local::LocalSqliteDbPlugin;
    use bcs_domain::{BindingStatus, BindingTarget, GroupChatScope, Visibility};

    fn test_db_error(operation: &'static str, err: DbError) -> ServiceError {
        ServiceError::InternalError(format!("test db {}: {}", operation, err))
    }

    async fn execute_schema(db: &LocalSqliteDbPlugin, sql: &'static str) -> ServiceResult<()> {
        db.execute(DbStatement::new(sql))
            .await
            .map(|_| ())
            .map_err(|err| test_db_error("schema", err))
    }

    async fn sqlite_db() -> ServiceResult<Arc<LocalSqliteDbPlugin>> {
        let db = LocalSqliteDbPlugin::new().map_err(|err| test_db_error("open sqlite", err))?;

        execute_schema(
            &db,
            "CREATE TABLE bcs_channel_bindings (
                id TEXT PRIMARY KEY,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                channel_type TEXT NOT NULL,
                account_ref TEXT NOT NULL,
                target_json TEXT NOT NULL,
                group_chat_scope TEXT,
                visibility TEXT NOT NULL,
                env TEXT NOT NULL,
                status TEXT NOT NULL,
                created_by TEXT,
                config_json TEXT NOT NULL
            )",
        )
        .await?;
        execute_schema(
            &db,
            "CREATE TABLE bcs_channel_conversations (
                binding_id TEXT NOT NULL,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                im_conversation_id TEXT NOT NULL,
                im_conversation_type TEXT NOT NULL,
                session_scope TEXT NOT NULL,
                im_user_id TEXT NOT NULL,
                bcs_session_id TEXT NOT NULL,
                last_active_at INTEGER NOT NULL,
                PRIMARY KEY (
                    binding_id,
                    im_conversation_id,
                    session_scope,
                    im_user_id
                )
            )",
        )
        .await?;
        execute_schema(
            &db,
            "CREATE TABLE bcs_channel_im_participants (
                channel_type TEXT NOT NULL,
                gmt_create TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                gmt_modified TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                account_ref TEXT NOT NULL,
                im_user_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                display_name TEXT,
                PRIMARY KEY (channel_type, account_ref, im_user_id)
            )",
        )
        .await?;

        Ok(Arc::new(db))
    }

    async fn sqlite_stores() -> ServiceResult<(
        Arc<dyn ChannelBindingRepoPort>,
        Arc<dyn ConversationSessionRepoPort>,
        Arc<dyn ImParticipantRepoPort>,
    )> {
        let db = sqlite_db().await?;
        let db_plugin: Arc<dyn DbPlugin> = db;

        Ok((
            Arc::new(DbChannelBindingStore::sqlite(db_plugin.clone(), "dev")),
            Arc::new(DbConversationSessionStore::sqlite(db_plugin.clone())),
            Arc::new(DbImParticipantStore::sqlite(db_plugin)),
        ))
    }

    fn binding() -> ChannelBinding {
        ChannelBinding {
            id: "binding_1".to_string(),
            channel_type: "dingtalk".to_string(),
            account_ref: "robot_1".to_string(),
            target: BindingTarget::Group {
                group_id: "group_1".to_string(),
            },
            group_chat_scope: Some(GroupChatScope::PerSender),
            outbound_visibility: Visibility::FullTranscript,
            env: "dev".to_string(),
            status: BindingStatus::Active,
            created_by: Some("creator".to_string()),
            config: serde_json::json!({
                "robot_code": "robot_1",
                "client_id": "client_1",
                "client_secret": "secret_1",
                "send_mode": {
                    "mode": "normal",
                    "message_type": "markdown"
                }
            }),
        }
    }

    fn conversation(
        session_scope: SessionScope,
        im_user_id: Option<&str>,
        bcs_session_id: &str,
        last_active_at: u64,
    ) -> ConversationSessionMap {
        ConversationSessionMap {
            binding_id: "binding_1".to_string(),
            im_conversation_id: "conversation_1".to_string(),
            im_conversation_type: "group".to_string(),
            session_scope,
            im_user_id: im_user_id.map(str::to_string),
            bcs_session_id: bcs_session_id.to_string(),
            last_active_at,
        }
    }

    fn participant(actor_id: &str, display_name: &str) -> ImParticipantMap {
        ImParticipantMap {
            channel_type: "dingtalk".to_string(),
            account_ref: "robot_1".to_string(),
            im_user_id: "staff_1".to_string(),
            actor_id: actor_id.to_string(),
            display_name: Some(display_name.to_string()),
        }
    }

    #[tokio::test]
    async fn sqlite_binding_crud_round_trip() -> ServiceResult<()> {
        let (binding_repo, _, _) = sqlite_stores().await?;
        let binding = binding();

        binding_repo.create(binding.clone()).await?;

        let got = binding_repo.get("binding_1").await?;
        match got {
            Some(got) => {
                assert_eq!(got, binding);
                assert_eq!(got.group_chat_scope, Some(GroupChatScope::PerSender));
            }
            None => panic!("expected binding_1 after create"),
        }

        let active = binding_repo
            .find_active_by_account("dingtalk".to_string(), "robot_1")
            .await?;
        assert_eq!(
            active.as_ref().map(|binding| binding.id.as_str()),
            Some("binding_1")
        );

        binding_repo.set_status("binding_1", false).await?;

        let disabled_active = binding_repo
            .find_active_by_account("dingtalk".to_string(), "robot_1")
            .await?;
        assert_eq!(disabled_active, None);

        let listed = binding_repo.list().await?;
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].id.as_str(), "binding_1");
        assert_eq!(listed[0].status, BindingStatus::Disabled);

        binding_repo.delete("binding_1").await?;
        assert_eq!(binding_repo.get("binding_1").await?, None);

        Ok(())
    }

    #[tokio::test]
    async fn sqlite_binding_list_by_target_filters_target_and_channel() -> ServiceResult<()> {
        let db = sqlite_db().await?;
        let db_plugin: Arc<dyn DbPlugin> = db;
        let binding_repo = DbChannelBindingStore::sqlite(db_plugin.clone(), "dev");
        let other_env_repo = DbChannelBindingStore::sqlite(db_plugin, "pre");

        let group_dingtalk = binding();
        binding_repo.create(group_dingtalk).await?;

        let mut group_other_channel = binding();
        group_other_channel.id = "binding_other_channel".to_string();
        group_other_channel.account_ref = "account_2".to_string();
        group_other_channel.channel_type = "test_im".to_string();
        binding_repo.create(group_other_channel).await?;

        let mut group_other_env = binding();
        group_other_env.id = "binding_other_env".to_string();
        group_other_env.account_ref = "account_pre".to_string();
        group_other_env.channel_type = "test_im".to_string();
        group_other_env.env = "pre".to_string();
        other_env_repo.create(group_other_env).await?;

        let mut other_group = binding();
        other_group.id = "binding_other_group".to_string();
        other_group.account_ref = "robot_2".to_string();
        other_group.target = BindingTarget::Group {
            group_id: "group_2".to_string(),
        };
        binding_repo.create(other_group).await?;

        let group_target = BindingTarget::Group {
            group_id: "group_1".to_string(),
        };
        let all_channels = binding_repo.list_by_target(&group_target, None).await?;
        assert_eq!(all_channels.len(), 2);

        let dingtalk = binding_repo
            .list_by_target(&group_target, Some("dingtalk"))
            .await?;
        assert_eq!(dingtalk.len(), 1);
        assert_eq!(dingtalk[0].id, "binding_1");

        assert_eq!(binding_repo.delete_by_target(&group_target).await?, 2);
        let remaining_group_bindings = binding_repo.list_by_target(&group_target, None).await?;
        assert!(remaining_group_bindings.is_empty());
        assert!(other_env_repo.get("binding_other_env").await?.is_some());
        assert!(binding_repo.get("binding_other_group").await?.is_some());

        Ok(())
    }

    #[tokio::test]
    async fn sqlite_binding_repo_isolates_environment_reads_and_writes() -> ServiceResult<()> {
        let db = sqlite_db().await?;
        let db_plugin: Arc<dyn DbPlugin> = db;
        let pre_repo = DbChannelBindingStore::sqlite(db_plugin.clone(), "pre");
        let prod_repo = DbChannelBindingStore::sqlite(db_plugin, "prod");

        let mut pre_binding = binding();
        pre_binding.id = "binding_pre".to_string();
        pre_binding.env = "pre".to_string();
        pre_repo.create(pre_binding).await?;

        let mut prod_binding = binding();
        prod_binding.id = "binding_prod".to_string();
        prod_binding.env = "prod".to_string();
        prod_repo.create(prod_binding.clone()).await?;

        let pre_items = pre_repo.list().await?;
        assert_eq!(pre_items.len(), 1);
        assert_eq!(pre_items[0].id, "binding_pre");
        assert_eq!(pre_repo.get("binding_prod").await?, None);

        let target = BindingTarget::Group {
            group_id: "group_1".to_string(),
        };
        let pre_target_items = pre_repo
            .list_by_target(&target, Some("dingtalk"))
            .await?;
        assert_eq!(pre_target_items.len(), 1);
        assert_eq!(pre_target_items[0].id, "binding_pre");

        let pre_active = pre_repo
            .find_active_by_account("dingtalk".to_string(), "robot_1")
            .await?;
        assert_eq!(
            pre_active.as_ref().map(|binding| binding.id.as_str()),
            Some("binding_pre")
        );

        pre_repo.set_status("binding_prod", false).await?;
        pre_repo
            .set_config("binding_prod", serde_json::json!({"changed": true}))
            .await?;
        pre_repo.delete("binding_prod").await?;

        let unchanged_prod = prod_repo
            .get("binding_prod")
            .await?
            .expect("prod binding must remain visible in prod");
        assert_eq!(unchanged_prod.status, BindingStatus::Active);
        assert_eq!(unchanged_prod.config, prod_binding.config);

        let mut mismatched = binding();
        mismatched.id = "binding_mismatched".to_string();
        mismatched.env = "prod".to_string();
        let error = pre_repo
            .create(mismatched)
            .await
            .expect_err("repository must reject a cross-environment write");
        assert!(error.to_string().contains("does not match repository env"));

        Ok(())
    }

    #[tokio::test]
    async fn sqlite_channel_tables_populate_audit_timestamps() -> ServiceResult<()> {
        let db = sqlite_db().await?;
        let db_plugin: Arc<dyn DbPlugin> = db;
        let binding_repo = DbChannelBindingStore::sqlite(db_plugin.clone(), "dev");
        let conversation_repo = DbConversationSessionStore::sqlite(db_plugin.clone());
        let participant_repo = DbImParticipantStore::sqlite(db_plugin.clone());

        binding_repo.create(binding()).await?;
        conversation_repo
            .upsert(conversation(
                SessionScope::Conversation,
                None,
                "session_1",
                100,
            ))
            .await?;
        participant_repo
            .upsert(participant("actor_1", "Alice"))
            .await?;

        for table in [
            "bcs_channel_bindings",
            "bcs_channel_conversations",
            "bcs_channel_im_participants",
        ] {
            let rows = db_plugin
                .query(DbStatement::new(format!(
                    "SELECT gmt_create, gmt_modified FROM {table} LIMIT 1"
                )))
                .await
                .map_err(|err| test_db_error("query audit timestamps", err))?;
            let row = rows.first().expect("expected audit timestamp row");
            assert!(
                row.get_string("gmt_create")
                    .map_err(|err| test_db_error("read gmt_create", err))?
                    .is_some()
            );
            assert!(
                row.get_string("gmt_modified")
                    .map_err(|err| test_db_error("read gmt_modified", err))?
                    .is_some()
            );
        }

        Ok(())
    }

    async fn query_string(
        db: &dyn DbPlugin,
        sql: impl Into<String>,
        column: &'static str,
    ) -> ServiceResult<String> {
        let rows = db
            .query(DbStatement::new(sql.into()))
            .await
            .map_err(|err| test_db_error("query string", err))?;
        rows.first()
            .expect("expected row")
            .get_string(column)
            .map_err(|err| test_db_error("read string", err))?
            .ok_or_else(|| ServiceError::InternalError(format!("missing {}", column)))
    }

    async fn force_old_modified(db: &dyn DbPlugin, table: &str) -> ServiceResult<()> {
        db.execute(DbStatement::new(format!(
            "UPDATE {table} SET gmt_modified = '2000-01-01 00:00:00'"
        )))
        .await
        .map(|_| ())
        .map_err(|err| test_db_error("force old modified", err))
    }

    #[tokio::test]
    async fn sqlite_write_paths_refresh_gmt_modified() -> ServiceResult<()> {
        let db = sqlite_db().await?;
        let db_plugin: Arc<dyn DbPlugin> = db;
        let binding_repo = DbChannelBindingStore::sqlite(db_plugin.clone(), "dev");
        let conversation_repo = DbConversationSessionStore::sqlite(db_plugin.clone());
        let participant_repo = DbImParticipantStore::sqlite(db_plugin.clone());

        binding_repo.create(binding()).await?;
        force_old_modified(db_plugin.as_ref(), "bcs_channel_bindings").await?;
        binding_repo.set_status("binding_1", false).await?;
        assert_ne!(
            query_string(
                db_plugin.as_ref(),
                "SELECT gmt_modified FROM bcs_channel_bindings WHERE id = 'binding_1'",
                "gmt_modified",
            )
            .await?,
            "2000-01-01 00:00:00"
        );

        force_old_modified(db_plugin.as_ref(), "bcs_channel_bindings").await?;
        binding_repo
            .set_config(
                "binding_1",
                serde_json::json!({"send_mode": {"mode": "normal"}}),
            )
            .await?;
        assert_ne!(
            query_string(
                db_plugin.as_ref(),
                "SELECT gmt_modified FROM bcs_channel_bindings WHERE id = 'binding_1'",
                "gmt_modified",
            )
            .await?,
            "2000-01-01 00:00:00"
        );

        conversation_repo
            .upsert(conversation(
                SessionScope::Conversation,
                None,
                "session_old",
                100,
            ))
            .await?;
        force_old_modified(db_plugin.as_ref(), "bcs_channel_conversations").await?;
        conversation_repo
            .upsert(conversation(
                SessionScope::Conversation,
                None,
                "session_new",
                200,
            ))
            .await?;
        assert_ne!(
            query_string(
                db_plugin.as_ref(),
                "SELECT gmt_modified FROM bcs_channel_conversations \
                 WHERE binding_id = 'binding_1' AND im_conversation_id = 'conversation_1'",
                "gmt_modified",
            )
            .await?,
            "2000-01-01 00:00:00"
        );

        participant_repo
            .upsert(participant("actor_1", "Alice"))
            .await?;
        force_old_modified(db_plugin.as_ref(), "bcs_channel_im_participants").await?;
        participant_repo
            .upsert(participant("actor_2", "Alice New"))
            .await?;
        assert_ne!(
            query_string(
                db_plugin.as_ref(),
                "SELECT gmt_modified FROM bcs_channel_im_participants \
                 WHERE channel_type = 'dingtalk' AND account_ref = 'robot_1' AND im_user_id = 'staff_1'",
                "gmt_modified",
            )
            .await?,
            "2000-01-01 00:00:00"
        );

        Ok(())
    }

    #[tokio::test]
    async fn sqlite_conversation_upsert_and_find_by_session() -> ServiceResult<()> {
        let (_, conversation_repo, _) = sqlite_stores().await?;

        conversation_repo
            .upsert(conversation(
                SessionScope::Conversation,
                None,
                "session_old",
                100,
            ))
            .await?;
        conversation_repo
            .upsert(conversation(
                SessionScope::Conversation,
                None,
                "session_new",
                200,
            ))
            .await?;

        let shared = conversation_repo
            .get(
                "binding_1",
                "conversation_1",
                SessionScope::Conversation,
                None,
            )
            .await?;
        match shared {
            Some(shared) => {
                assert_eq!(shared.bcs_session_id, "session_new");
                assert_eq!(shared.im_user_id, None);
                assert_eq!(shared.last_active_at, 200);
            }
            None => panic!("expected shared conversation mapping"),
        }

        conversation_repo
            .upsert(conversation(
                SessionScope::PerSender,
                Some("staff_1"),
                "session_sender",
                300,
            ))
            .await?;

        let per_sender = conversation_repo
            .get(
                "binding_1",
                "conversation_1",
                SessionScope::PerSender,
                Some("staff_1"),
            )
            .await?;
        match per_sender {
            Some(per_sender) => assert_eq!(per_sender.bcs_session_id, "session_sender"),
            None => panic!("expected per-sender conversation mapping"),
        }

        let by_session = conversation_repo
            .find_by_session("binding_1", "session_sender")
            .await?;
        match by_session {
            Some(by_session) => {
                assert_eq!(by_session.session_scope, SessionScope::PerSender);
                assert_eq!(by_session.im_user_id.as_deref(), Some("staff_1"));
            }
            None => panic!("expected find_by_session result"),
        }
        let by_bcs_session = conversation_repo
            .list_by_bcs_session("session_sender")
            .await?;
        assert_eq!(by_bcs_session.len(), 1);
        assert_eq!(by_bcs_session[0].binding_id, "binding_1");
        assert_eq!(by_bcs_session[0].session_scope, SessionScope::PerSender);

        Ok(())
    }

    #[tokio::test]
    async fn sqlite_participant_upsert_round_trip() -> ServiceResult<()> {
        let (_, _, participant_repo) = sqlite_stores().await?;

        participant_repo
            .upsert(participant("actor_old", "Old Name"))
            .await?;
        participant_repo
            .upsert(participant("actor_new", "New Name"))
            .await?;

        let got = participant_repo
            .get("dingtalk".to_string(), "robot_1", "staff_1")
            .await?;
        match got {
            Some(got) => {
                assert_eq!(got.actor_id, "actor_new");
                assert_eq!(got.display_name.as_deref(), Some("New Name"));
            }
            None => panic!("expected participant mapping"),
        }

        Ok(())
    }
}
