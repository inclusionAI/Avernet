//! Processor-specific reader reconstruction and explicit non-writing aborts.
use crate::runtime::*;
use bcs_dist_model::*;
use bcs_service_api::{port::repo::distributed::common::*, types::dist_runtime::RuntimeError};
use bcs_service_api::port::repo::distributed::session::AcquiredSession;
impl WorkerRuntime {
    pub(crate) fn defer_local_reacquire(
        &self,
        owner: &SessionOwnership,
    ) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        if let Some(claim) = state.shards.get(&owner.shard).cloned()
            && (state.retries.contains_key(&owner.sid)
                || state.retries.len() < self.config.max_active_sessions)
        {
            state.retries.insert(
                owner.sid.clone(),
                (
                    claim,
                    tokio::time::Instant::now() + self.config.check_interval,
                ),
            );
        }
        Ok(())
    }
    /// Called only after a durable control fence and closed admission gate.
    /// Removing local cache/watch state never acknowledges committed effects or
    /// changes the retained checkpoint, isolation evidence or pending index.
    pub(crate) async fn park_isolated(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        self.park_local(owner).await
    }
    /// The processor has verified this exact committed head and retained no
    /// unconfirmed candidate. Replay is idempotent from the durable outbox;
    /// releasing its cache/watch is neither effect completion nor Inbox ack.
    pub(crate) async fn park_committed_replay(
        &self,
        acquired: &AcquiredSession,
    ) -> Result<(), RuntimeError> {
        {
            let state = self.state()?;
            let session = state
                .sessions
                .get(&acquired.ownership.sid)
                .ok_or_else(|| err("session_not_active"))?;
            if session.acquired != *acquired
                || !matches!(session.phase, Phase::Effects | Phase::Recovering)
                || session.pending_schedule
                || acquired.last_commit.is_none()
                || acquired
                    .envelope
                    .checkpoint
                    .outbox
                    .as_ref()
                    .is_none_or(|batch| batch.effects.is_empty())
            {
                return Err(err("session_replay_park_state"));
            }
        }
        self.park_local(&acquired.ownership).await
    }
    async fn park_local(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        {
            let state = self.state()?;
            if state
                .sessions
                .get(&owner.sid)
                .is_none_or(|s| s.acquired.ownership != *owner)
            {
                return Err(err("session_owner_changed"));
            }
        }
        let feed = self
            .feeds
            .lock()
            .map_err(|_| err("worker_feed_poisoned"))?
            .get(&owner.shard)
            .cloned();
        if let Some(feed) = feed {
            self.bounded(async {
                if let Some(reader) = feed.lock().await.as_mut() {
                    reader.unwatch(&owner.sid).await?;
                }
                Ok(())
            })
            .await?;
        }
        self.bounded(self.sessions.release_local(owner)).await?;
        let mut state = self.state()?;
        if state
            .sessions
            .get(&owner.sid)
            .is_some_and(|s| s.acquired.ownership == *owner)
        {
            state.sessions.remove(&owner.sid);
            if let Some(claim) = state.shards.get(&owner.shard).cloned()
                && (state.retries.contains_key(&owner.sid)
                    || state.retries.len() < self.config.max_active_sessions)
            {
                state.retries.insert(
                    owner.sid.clone(),
                    (
                        claim,
                        tokio::time::Instant::now() + self.config.check_interval,
                    ),
                );
            }
            state.made_progress();
        }
        drop(state);
        self.close_unused_feeds()?;
        Ok(())
    }
    pub(crate) fn control_fenced_round(
        &self,
        owner: &SessionOwnership,
    ) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        let session = state
            .sessions
            .get_mut(&owner.sid)
            .ok_or_else(|| err("session_not_active"))?;
        if session.acquired.ownership != *owner
            || !matches!(session.phase, Phase::Running | Phase::Unknown)
        {
            return Err(err("session_round_state"));
        }
        session.phase = Phase::Idle;
        Ok(())
    }

    pub(crate) fn round_aborted(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        let session = state
            .sessions
            .get_mut(&owner.sid)
            .ok_or_else(|| err("session_not_active"))?;
        if session.acquired.ownership != *owner || session.phase != Phase::Running {
            return Err(err("session_round_state"));
        }
        session.phase = Phase::Idle;
        Ok(())
    }
    pub(crate) async fn rewatch_inputs(
        &self,
        owner: &SessionOwnership,
    ) -> Result<(), RuntimeError> {
        let feed = self.feed(owner.shard).await?;
        let mut feed = feed.lock().await;
        let consumed = {
            let state = self.state()?;
            let session = state
                .sessions
                .get(&owner.sid)
                .ok_or_else(|| err("session_not_active"))?;
            if session.acquired.ownership != *owner {
                return Err(err("session_owner_changed"));
            }
            session.acquired.envelope.checkpoint.consumed.clone()
        };
        self.bounded(async {
            feed.as_mut()
                .ok_or_else(reader_rebuilding)?
                .watch(owner.sid.clone(), consumed)
                .await
        })
        .await
    }
    pub(crate) async fn pause_inputs(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        let feed = self.feed(owner.shard).await?;
        self.bounded(async {
            feed.lock()
                .await
                .as_mut()
                .ok_or_else(reader_rebuilding)?
                .unwatch(&owner.sid)
                .await
        })
        .await
    }
    pub(crate) async fn rebuild_feed(&self, shard: ShardId) -> Result<(), RuntimeError> {
        self.reader_restarts
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let shared = self
            .feeds
            .lock()
            .map_err(|_| err("worker_feed_poisoned"))?
            .get(&shard)
            .cloned();
        if let Some(shared) = shared {
            // Release reader/watch permits BEFORE opening the replacement. None
            // is explicit unavailable state, also recoverable if this is canceled.
            *shared.lock().await = None;
        }
        self.feed(shard).await?;
        Ok(())
    }
    /// Only durable fencing authorizes abandoning unknown local work. Its old
    /// candidate can no longer commit; committed effects remain in durable state.
    pub(crate) async fn forget_fenced(&self, owner: &SessionOwnership) -> Result<(), RuntimeError> {
        let feed = self.feed(owner.shard).await?;
        self.bounded(async {
            feed.lock()
                .await
                .as_mut()
                .ok_or_else(reader_rebuilding)?
                .unwatch(&owner.sid)
                .await
        })
        .await?;
        self.bounded(self.sessions.release_local(owner)).await?;
        let mut state = self.state()?;
        if state
            .sessions
            .get(&owner.sid)
            .is_some_and(|s| s.acquired.ownership == *owner)
        {
            state.sessions.remove(&owner.sid);
        }
        Ok(())
    }
}
