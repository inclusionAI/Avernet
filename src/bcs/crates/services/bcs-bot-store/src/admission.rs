//! Request-local identity reads and serialization for the existing registries.
use bcs_service_api::port::repo::{
    BotIdentity as Identity, BotIdentityOperationPort, BotIdentityUpdate,
    BotMemoryIdentity as MemoryIdentity,
};
use bcs_service_api::{ServiceError, ServiceResult};
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

#[async_trait::async_trait]
pub(crate) trait StreamingStore: Send + Sync {
    fn identity_locks(&self) -> &IdentityLocks;
    async fn memory_token_owner(&self, token: &str) -> Option<String>;
    async fn memory_identity(&self, id: &str) -> Option<MemoryIdentity>;
    async fn stored_by_id(&self, id: &str) -> ServiceResult<Option<Identity>>;
    async fn stored_by_token(&self, token: &str) -> ServiceResult<Option<Identity>>;
    async fn replace_token(&self, identity: &Identity, token: &str) -> ServiceResult<()>;
    async fn attach(&self, identity: Identity, token: &str) -> ServiceResult<()>;
}

enum Lookup {
    NotLoaded,
    Loaded(Option<Identity>), // None is a successful miss, never a read error.
}

struct IdentityOperation<'a, S: StreamingStore> {
    store: &'a S,
    token_lookup: Option<(String, Option<String>, Option<Identity>)>,
    lookup_epoch: Option<u64>,
    locked: Option<(String, IdentityGuard)>,
    stored: Lookup,
}

pub(crate) fn begin<S: StreamingStore>(store: &S) -> Box<dyn BotIdentityOperationPort + '_> {
    Box::new(IdentityOperation {
        store,
        token_lookup: None,
        lookup_epoch: None,
        locked: None,
        stored: Lookup::NotLoaded,
    })
}

fn invalid_scope(message: &str) -> ServiceError {
    ServiceError::InternalError(message.to_owned())
}

#[async_trait::async_trait]
impl<S: StreamingStore> BotIdentityOperationPort for IdentityOperation<'_, S> {
    async fn token_owner(&mut self, token: &str) -> ServiceResult<Option<String>> {
        if self.locked.is_some() {
            return Err(invalid_scope("token lookup must precede identity locking"));
        }
        if let Some((key, owner, _)) = &self.token_lookup {
            if key == token {
                return Ok(owner.clone());
            }
            return Err(invalid_scope("an identity operation resolves one token"));
        }
        let memory_owner = self.store.memory_token_owner(token).await;
        self.lookup_epoch = self.store.identity_locks().read_epoch();
        let row = if memory_owner.is_none() {
            self.store.stored_by_token(token).await?
        } else {
            None
        };
        let owner = memory_owner.or_else(|| row.as_ref().map(|r| r.id.clone()));
        self.token_lookup = Some((token.to_owned(), owner.clone(), row));
        Ok(owner)
    }

    async fn lock_identity(&mut self, id: &str) -> ServiceResult<Option<MemoryIdentity>> {
        if self.locked.is_some() {
            return Err(invalid_scope("an identity operation locks one Bot once"));
        }
        let guard = self.store.identity_locks().lock(id).await;
        let memory = self.store.memory_identity(id).await;
        self.locked = Some((id.to_owned(), guard));
        Ok(memory)
    }

    async fn stored_identity(&mut self) -> ServiceResult<Option<Identity>> {
        if let Lookup::Loaded(row) = &self.stored {
            return Ok(row.clone());
        }
        let (id, guard) = self
            .locked
            .as_ref()
            .ok_or_else(|| invalid_scope("persistent read requires the identity lock"))?;
        let reusable = self
            .token_lookup
            .as_ref()
            .and_then(|(_, _, row)| row.as_ref())
            .filter(|row| {
                row.id == *id
                    && self.lookup_epoch.is_some()
                    && self.lookup_epoch == guard.preceding_epoch
            });
        let row = match reusable {
            Some(row) => Some(row.clone()),
            None => self.store.stored_by_id(id).await?,
        };
        self.stored = Lookup::Loaded(row.clone());
        Ok(row)
    }

    async fn apply(self: Box<Self>, update: BotIdentityUpdate) -> ServiceResult<()> {
        let (id, _) = self
            .locked
            .as_ref()
            .ok_or_else(|| invalid_scope("identity update requires the identity lock"))?;
        if update.identity.id != *id {
            return Err(invalid_scope(
                "identity update does not match the locked Bot",
            ));
        }
        let loaded = match &self.stored {
            Lookup::NotLoaded => {
                return Err(invalid_scope(
                    "identity update requires a successful persistent read",
                ))
            }
            Lookup::Loaded(row) => row,
        };
        if update.replace_persistent_token {
            let expected = loaded.as_ref().ok_or_else(|| {
                invalid_scope("token replacement requires a loaded persistent row")
            })?;
            self.store.replace_token(expected, &update.token).await?;
        }
        self.store.attach(update.identity, &update.token).await
    }
}
