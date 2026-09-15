//! Shared streaming identity decisions for SQL and local-file repositories.
use bcs_service_api::{
    BotCapabilities, BotConnectParams, BotConnectResult, ConnectError, is_mock_token,
};
use std::collections::HashMap;
use std::sync::{Arc, Weak};
use tokio::sync::{Mutex, OwnedMutexGuard};

#[derive(Debug, Default)]
pub(crate) struct IdentityLocks {
    keys: Mutex<HashMap<String, Weak<Mutex<()>>>>,
    epoch: Arc<std::sync::atomic::AtomicU64>,
    active: Arc<std::sync::atomic::AtomicUsize>,
}

pub(crate) struct IdentityGuard {
    _guard: OwnedMutexGuard<()>,
    epoch: Arc<std::sync::atomic::AtomicU64>,
    active: Arc<std::sync::atomic::AtomicUsize>,
    preceding_epoch: Option<u64>,
}

impl Drop for IdentityGuard {
    fn drop(&mut self) {
        use std::sync::atomic::Ordering::SeqCst;
        self.epoch.fetch_add(1, SeqCst);
        self.active.fetch_sub(1, SeqCst);
    }
}

impl IdentityLocks {
    // A token-only lookup cannot lock an unknown Bot ID in advance. Reuse its
    // row only if no local identity writer overlapped the read or intervened.
    // Concurrent writes conservatively cause one fresh read under the ID lock.
    pub(crate) fn read_epoch(&self) -> Option<u64> {
        use std::sync::atomic::Ordering::SeqCst;
        let epoch = self.epoch.load(SeqCst);
        if self.active.load(SeqCst) == 0 && self.epoch.load(SeqCst) == epoch {
            Some(epoch)
        } else {
            None
        }
    }

    pub(crate) async fn lock(&self, id: &str) -> IdentityGuard {
        use std::sync::atomic::Ordering::SeqCst;
        let mut locks = self.keys.lock().await;
        if locks.len() >= 256 {
            locks.retain(|_, lock| lock.strong_count() > 0);
        }
        let lock = locks.get(id).and_then(Weak::upgrade).unwrap_or_else(|| {
            let lock = Arc::new(Mutex::new(()));
            locks.insert(id.to_owned(), Arc::downgrade(&lock));
            lock
        });
        drop(locks);
        let guard = lock.lock_owned().await;
        let preceding_epoch = self.read_epoch();
        self.active.fetch_add(1, SeqCst);
        self.epoch.fetch_add(1, SeqCst);
        IdentityGuard {
            _guard: guard,
            epoch: self.epoch.clone(),
            active: self.active.clone(),
            preceding_epoch,
        }
    }
}

// Never derive Debug: the snapshot contains a credential.
#[derive(Clone)]
pub(crate) struct Identity {
    pub id: String,
    pub token: Option<String>,
    pub deleted: bool,
    pub capabilities: BotCapabilities,
    pub env: Option<String>,
    pub created_by: Option<String>,
    pub actor_kind: bcs_service_api::ActorKind,
    pub status: bcs_service_api::ActorStatus,
}

pub(crate) struct MemoryIdentity {
    pub identity: Identity,
    pub expired: bool,
    pub connected: bool,
}

#[async_trait::async_trait]
pub(crate) trait StreamingStore: Send + Sync {
    fn identity_locks(&self) -> &IdentityLocks;
    async fn memory_token_owner(&self, token: &str) -> Option<String>;
    async fn memory_identity(&self, id: &str) -> Option<MemoryIdentity>;
    async fn stored_by_id(&self, id: &str) -> Result<Option<Identity>, ConnectError>;
    async fn stored_by_token(&self, token: &str) -> Result<Option<Identity>, ConnectError>;
    async fn promote(&self, identity: &Identity, token: &str) -> Result<(), ConnectError>;
    async fn attach(&self, identity: Identity, token: &str) -> Result<(), ConnectError>;
}

/// Holds the identity lock across lookup and commit, never the global Bot map.
/// A database miss is carried as a value and is not queried a second time.
pub(crate) async fn connect<S: StreamingStore>(
    store: &S,
    params: BotConnectParams,
) -> Result<BotConnectResult, ConnectError> {
    let token = params.token.as_deref();
    let memory_owner = match token {
        Some(token) => store.memory_token_owner(token).await,
        None => None,
    };
    // Token lookup is needed only when memory cannot locate the identity.
    // Recheck this pre-lock snapshot below: local registration may race it.
    let lookup_epoch = store.identity_locks().read_epoch();
    let token_row = if memory_owner.is_none() {
        match token {
            Some(token) => store.stored_by_token(token).await?,
            None => None,
        }
    } else {
        None
    };
    let token_owner = memory_owner.or_else(|| token_row.as_ref().map(|r| r.id.clone()));
    let id = token_owner
        .clone()
        .or(params.bot_id)
        .unwrap_or_else(|| format!("bot_{}", &uuid::Uuid::new_v4().simple().to_string()[..8]));
    if id.is_empty() {
        return Err(ConnectError::InvalidBotId);
    }
    let guard = store.identity_locks().lock(&id).await;
    let memory = store.memory_identity(&id).await;
    let owns_current = memory.as_ref().is_some_and(|m| {
        token.is_some() && m.identity.token.as_deref() == token && !token.is_some_and(is_mock_token)
    });
    // A token index entry or token lookup only locates a candidate identity.
    // It may carry a newly rotated credential while memory still has the old
    // token. Resolve that candidate against storage before rejecting it.
    let credential_candidate = token.is_some() && token_owner.is_some();
    // An authenticated reconnect keeps the existing token. An active connection
    // must never be reclaimed as a new temporary registration, even if expired.
    if memory.as_ref().is_some_and(|m| m.connected) && !owns_current && !credential_candidate {
        return Err(ConnectError::AlreadyConnected(id));
    }
    if let Some(current) = memory
        .as_ref()
        .filter(|m| !m.expired && !m.identity.deleted)
    {
        if !owns_current
            && !credential_candidate
            && !current.identity.token.as_deref().is_some_and(is_mock_token)
        {
            return Err(ConnectError::AlreadyRegistered(id));
        }
    }
    // Reuse a complete token lookup when its local identity epoch is stable.
    let stored =
        if token_row.is_some() && lookup_epoch.is_some() && lookup_epoch == guard.preceding_epoch {
            token_row
        } else {
            store.stored_by_id(&id).await?
        };
    let (identity, assigned_token, is_new, reason) = match stored {
        Some(identity) if identity.deleted => {
            tracing::warn!(bot_id = %id, reason = "deleted_identity", "bot.streaming.rejected");
            return Err(ConnectError::AlreadyRegistered(id));
        }
        Some(mut identity) if identity.token.as_deref().is_some_and(is_mock_token) => {
            if memory.as_ref().is_some_and(|m| m.connected) {
                return Err(ConnectError::AlreadyConnected(id));
            }
            if let Some(current) = &memory {
                identity.capabilities.agent_token =
                    current.identity.capabilities.agent_token.clone();
            }
            let assigned = uuid::Uuid::new_v4().to_string();
            store.promote(&identity, &assigned).await?;
            (identity, assigned, true, "promoted_mock")
        }
        Some(mut identity) if token.is_some() && identity.token.as_deref() == token => {
            if let Some(current) = &memory {
                identity.capabilities.agent_token =
                    current.identity.capabilities.agent_token.clone();
            }
            (identity, token.unwrap().to_owned(), false, "reconnected")
        }
        Some(_) => return Err(ConnectError::AlreadyRegistered(id)),
        None if owns_current && memory.as_ref().is_some_and(|m| !m.expired || m.connected) => (
            memory.as_ref().unwrap().identity.clone(),
            token.unwrap().to_owned(),
            false,
            "reconnected_temporary",
        ),
        None => {
            if memory.as_ref().is_some_and(|m| m.connected) {
                return Err(ConnectError::AlreadyConnected(id));
            }
            // A live temporary identity is protected even without persistence.
            if memory
                .as_ref()
                .is_some_and(|m| !m.expired || m.identity.deleted)
            {
                return Err(ConnectError::AlreadyRegistered(id));
            }
            let identity = Identity {
                id: id.clone(),
                token: None,
                deleted: false,
                capabilities: BotCapabilities::default(),
                env: None,
                created_by: None,
                actor_kind: bcs_service_api::ActorKind::Bot,
                status: bcs_service_api::ActorStatus::Online,
            };
            let reason = if memory.is_some() {
                "reclaimed_expired_temporary"
            } else {
                "created"
            };
            (identity, uuid::Uuid::new_v4().to_string(), true, reason)
        }
    };
    store.attach(identity, &assigned_token).await?;
    tracing::info!(request_id = %bcs_observability::CurrentRequestId, bot_id = %id, reason,
        "bot.streaming.admitted");
    Ok(BotConnectResult {
        is_new,
        bot_uuid: id,
        token: assigned_token,
    })
}
