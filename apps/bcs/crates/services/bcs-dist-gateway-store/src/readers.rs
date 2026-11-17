//! One blocking read per served session, shared by this process's subscribers.
//!
//! A reader starts with the first subscriber of a session and stops after the
//! last one left. Each read names exactly one key (decision 23). The reader
//! never waits for a subscriber: batches go out on a bounded broadcast channel,
//! and a subscriber that falls too far behind loses its place, not the others.

use bcs_dist_store::error::from_read;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::stream::{ReadOptions, StreamEntry, StreamId, StreamRead, StreamStorePlugin};
use std::collections::HashMap;
use std::num::NonZeroU32;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;
use tokio::sync::broadcast;

/// Consecutive failed reads after which a reader gives up and tells its
/// subscribers; they resume from their last position.
const MAX_READ_FAILURES: u32 = 3;
const FAILURE_BACKOFF: Duration = Duration::from_millis(200);

#[derive(Debug, Clone)]
pub(crate) enum Message {
    Entries(Arc<Vec<StreamEntry>>),
    Failed(StoreError),
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct ReaderConfig {
    pub read_block: Duration,
    pub read_batch: NonZeroU32,
    pub subscriber_buffer: usize,
}

struct Session {
    id: u64,
    tx: broadcast::Sender<Message>,
    /// Last entry id handed to the channel (or the start position).
    position: StreamId,
    subscribers: usize,
}

struct Inner {
    stream: Arc<dyn StreamStorePlugin>,
    config: ReaderConfig,
    state: Mutex<State>,
}

#[derive(Default)]
struct State {
    sessions: HashMap<String, Session>,
    next_id: u64,
}

impl Inner {
    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

#[derive(Clone)]
pub(crate) struct SessionReaders {
    inner: Arc<Inner>,
}

/// A place in a session's fan-out. Dropping it leaves the session.
pub(crate) struct Subscription {
    pub rx: broadcast::Receiver<Message>,
    /// The reader's position when this subscriber joined: everything after it
    /// arrives on `rx`; anything the subscriber still needs up to it must be
    /// read separately (catch-up).
    pub joined_at: StreamId,
    _guard: Guard,
}

struct Guard {
    inner: Arc<Inner>,
    key: String,
    id: u64,
}

impl Drop for Guard {
    fn drop(&mut self) {
        let mut state = self.inner.lock();
        if let Some(session) = state.sessions.get_mut(&self.key)
            && session.id == self.id
        {
            session.subscribers = session.subscribers.saturating_sub(1);
        }
    }
}

impl SessionReaders {
    pub(crate) fn new(stream: Arc<dyn StreamStorePlugin>, config: ReaderConfig) -> Self {
        Self {
            inner: Arc::new(Inner {
                stream,
                config,
                state: Mutex::new(State::default()),
            }),
        }
    }

    /// Number of sessions with a running reader (one blocking read each).
    pub(crate) fn active(&self) -> usize {
        self.inner.lock().sessions.len()
    }

    /// Joins the fan-out of the stream `key`. Must be called inside a Tokio
    /// runtime. A new reader starts at `after`; an existing one is never moved.
    pub(crate) fn join(&self, key: &str, after: StreamId) -> Subscription {
        let mut state = self.inner.lock();
        let (rx, joined_at, id) = match state.sessions.get_mut(key) {
            Some(session) => {
                session.subscribers += 1;
                (session.tx.subscribe(), session.position, session.id)
            }
            None => {
                let id = state.next_id;
                state.next_id += 1;
                let (tx, rx) = broadcast::channel(self.inner.config.subscriber_buffer.max(1));
                state.sessions.insert(
                    key.to_owned(),
                    Session {
                        id,
                        tx,
                        position: after,
                        subscribers: 1,
                    },
                );
                tokio::spawn(run(self.inner.clone(), key.to_owned(), id, after));
                (rx, after, id)
            }
        };
        Subscription {
            rx,
            joined_at,
            _guard: Guard {
                inner: self.inner.clone(),
                key: key.to_owned(),
                id,
            },
        }
    }
}

async fn run(inner: Arc<Inner>, key: String, id: u64, mut position: StreamId) {
    let mut failures = 0u32;
    loop {
        {
            let mut state = inner.lock();
            match state.sessions.get(&key) {
                Some(session) if session.id == id && session.subscribers > 0 => {}
                Some(session) if session.id == id => {
                    state.sessions.remove(&key);
                    return;
                }
                _ => return,
            }
        }
        let read = inner
            .stream
            .read(
                &[StreamRead {
                    key: key.clone(),
                    after: Some(position),
                }],
                ReadOptions {
                    max_entries: inner.config.read_batch,
                    block: Some(inner.config.read_block),
                },
            )
            .await;
        match read {
            Ok(batches) => {
                failures = 0;
                let entries = batches
                    .into_iter()
                    .next()
                    .map(|batch| batch.page.entries)
                    .unwrap_or_default();
                let Some(last) = entries.last().map(|entry| entry.id) else {
                    continue;
                };
                position = last;
                let mut state = inner.lock();
                if let Some(session) = state.sessions.get_mut(&key)
                    && session.id == id
                {
                    session.position = last;
                    // No receiver left is fine: the next loop turn stops the reader.
                    let _ = session.tx.send(Message::Entries(Arc::new(entries)));
                }
            }
            Err(error) => {
                failures += 1;
                tracing::warn!(?error, failures, "session ev read failed");
                if failures >= MAX_READ_FAILURES {
                    let mut state = inner.lock();
                    if state.sessions.get(&key).is_some_and(|s| s.id == id)
                        && let Some(session) = state.sessions.remove(&key)
                    {
                        let _ = session.tx.send(Message::Failed(from_read(&error)));
                    }
                    return;
                }
                tokio::time::sleep(FAILURE_BACKOFF.min(inner.config.read_block)).await;
            }
        }
    }
}
