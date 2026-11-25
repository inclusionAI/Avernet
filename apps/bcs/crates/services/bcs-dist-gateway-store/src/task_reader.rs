//! The `TaskReaderPort` provider.
//!
//! - `restore` returns the stored task snapshot as is (decision 30: no folding
//!   of later `ev` entries in the MVP) with a cursor at its coverage, so a
//!   subscriber receives the entries after it as events.
//! - `follow` yields one item per event of the task, each with the cursor of
//!   that event, from the shared per-session reader (plus a catch-up read for
//!   a subscriber that starts behind it).
//! - `list` pages the per-session task index written by admission.

use crate::page;
use crate::position::{self, START};
use crate::readers::{Message, ReaderConfig, SessionReaders, Subscription};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::capability;
use bcs_dist_store::codec::{decode_ev_entry, decode_json, ev_entry_tid, stream_id};
use bcs_dist_store::error::from_read;
use bcs_dist_store::layout::KeyLayout;
use bcs_service_api::port::repo::distributed::common::{Page, PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::result::ResultStorePort;
use bcs_service_api::port::repo::distributed::task_feed::*;
use bcs_store_api::StoreCapability;
use bcs_store_api::document::DocumentStorePlugin;
use bcs_store_api::stream::{StreamEntry, StreamId, StreamStorePlugin};
use bcs_store_api::zset::{ZSetRange, ZSetStorePlugin};
use futures::StreamExt;
use futures::stream::BoxStream;
use std::collections::VecDeque;
use std::num::NonZeroU32;
use std::ops::Bound;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::broadcast::error::RecvError;

/// Code of the error a subscriber gets when it reads too slowly; it resumes
/// from its last cursor.
pub const SUBSCRIBER_LAGGED: &str = "subscriber_lagged";

/// Task snapshots read at once while listing.
const LIST_READ_CONCURRENCY: usize = 8;

/// Settings of the Gateway's readers.
#[derive(Debug, Clone)]
pub struct GatewayStoreConfig {
    /// Identifies the store in replay tokens, e.g. `tbase`.
    pub backend: BackendId,
    /// Longest wait of one blocking read before it is issued again.
    pub read_block: Duration,
    /// Entries per read and per catch-up page.
    pub read_batch: NonZeroU32,
    /// Batches a subscriber may fall behind before its stream is closed.
    pub subscriber_buffer: usize,
    /// Time a blocking read may take beyond `read_block` before it counts as
    /// a failed read.
    pub read_grace: Duration,
}

impl GatewayStoreConfig {
    pub fn new(backend: BackendId) -> Self {
        Self {
            backend,
            read_block: Duration::from_secs(1),
            read_batch: NonZeroU32::new(100).unwrap_or(NonZeroU32::MIN),
            subscriber_buffer: 256,
            read_grace: ReaderConfig::DEFAULT_GRACE,
        }
    }
}

struct Shared {
    documents: Arc<dyn DocumentStorePlugin>,
    zset: Arc<dyn ZSetStorePlugin>,
    stream: Arc<dyn StreamStorePlugin>,
    results: Arc<dyn ResultStorePort>,
    layout: KeyLayout,
    config: GatewayStoreConfig,
    readers: SessionReaders,
}

pub struct StoreTaskReader {
    shared: Arc<Shared>,
}

impl StoreTaskReader {
    /// Fails when a store lacks `DocumentCas` (the namespace `task` is created
    /// and published in), `ZSet` or `Stream`.
    pub fn new(
        documents: Arc<dyn DocumentStorePlugin>,
        zset: Arc<dyn ZSetStorePlugin>,
        stream: Arc<dyn StreamStorePlugin>,
        results: Arc<dyn ResultStorePort>,
        layout: KeyLayout,
        config: GatewayStoreConfig,
    ) -> Result<Self, StoreError> {
        capability::require(&[
            (documents.as_ref(), StoreCapability::DocumentCas),
            (zset.as_ref(), StoreCapability::ZSet),
            (stream.as_ref(), StoreCapability::Stream),
        ])?;
        if config.read_block.is_zero() {
            return Err(invalid("read_block_zero"));
        }
        let readers = SessionReaders::new(
            stream.clone(),
            ReaderConfig {
                read_block: config.read_block,
                read_batch: config.read_batch,
                subscriber_buffer: config.subscriber_buffer,
                read_grace: config.read_grace,
            },
        );
        Ok(Self {
            shared: Arc::new(Shared {
                documents,
                zset,
                stream,
                results,
                layout,
                config,
                readers,
            }),
        })
    }

    /// Sessions this process currently holds a blocking read for.
    pub fn served_sessions(&self) -> usize {
        self.shared.readers.active()
    }
}

impl Shared {
    async fn snapshot(&self, tid: &Tid) -> Result<Option<TaskSnapshot>, StoreError> {
        let key = self.layout.task(tid)?;
        match self.documents.read_document(&key).await.map_err(|e| from_read(&e))? {
            Some(doc) => decode_json(doc.value).map(Some),
            None => Ok(None),
        }
    }

    /// The task as stored: the live snapshot, or the final record once the
    /// task key expired. Another scope's task is reported as absent.
    ///
    /// Strict-group tasks follow the authoritative cut (plan T7): an
    /// archive row alone never authorizes a terminal presentation while the
    /// round's terminal authority window is unsettled, and a stale snapshot
    /// that still says Working is likewise not success. The retained
    /// terminal proof sealed by the authority commit is the one cut that may
    /// disclose the archived terminal here.
    async fn stored(&self, scope: &TaskScope, tid: &Tid) -> Result<Option<Stored>, StoreError> {
        match self.snapshot(tid).await? {
            Some(snapshot) if &snapshot.scope == scope => {
                if is_terminal(snapshot.content.status)
                    && !self.strict_terminal_viewable(&snapshot.sid, tid).await?
                {
                    return Ok(None);
                }
                Ok(Some(Stored::Live(snapshot)))
            }
            Some(_) => Ok(None),
            None => match self.results.get_final(scope, tid).await? {
                Some(record)
                    if !self.strict_terminal_viewable(&record.sid, tid).await? =>
                {
                    Ok(None)
                }
                Some(record) => Ok(Some(Stored::Archived(record))),
                None => Ok(None),
            },
        }
    }

    /// May this task's terminal be disclosed on the old read path? For
    /// strictly claimed sessions only through the retained authority
    /// (`group_terminal` sealed by the terminal CAS, cold-confirmed by the
    /// spine); every other strict state — an archive row alone or an old
    /// snapshot — stays invisible as success. Non-strict sessions keep the
    /// generic behavior.
    async fn strict_terminal_viewable(&self, sid: &Sid, tid: &Tid) -> Result<bool, StoreError> {
        let retained =
            bcs_dist_store::group_admission::probe_strict_license(&self.documents, &self.layout, sid)
                .await?;
        if retained.is_none() {
            return Ok(true);
        }
        Ok(bcs_dist_store::group_terminal::probe_retained_terminal(
            &self.documents,
            &self.layout,
            sid,
        )
        .await?
        .is_some_and(|proof| proof.key.tid == *tid))
    }

    /// Id of the newest entry of a stream; `None` when it has none.
    async fn head(&self, key: &str) -> Result<Option<StreamId>, StoreError> {
        let page = self
            .stream
            .range(key, Bound::Unbounded, Bound::Unbounded, NonZeroU32::MIN, true)
            .await
            .map_err(|e| from_read(&e))?;
        Ok(page.entries.first().map(|entry| entry.id))
    }

    /// Can `at` be followed for this task? Not when the task is finished (its
    /// entries may be trimmed at any time), when `at` is before the snapshot's
    /// coverage (those entries may be trimmed while it runs), or when `at` is
    /// beyond the newest entry (no reader ever handed out such a position, and
    /// a shared reader started there would deliver nothing to anyone).
    ///
    /// `Some(head)` = yes, with the stream's newest entry id (or the start).
    async fn followable(
        &self,
        snapshot: &TaskSnapshot,
        at: StreamId,
    ) -> Result<Option<StreamId>, StoreError> {
        if is_terminal(snapshot.content.status)
            || snapshot.snapshot_event_id.is_some_and(|covered| at < stream_id(covered))
        {
            return Ok(None);
        }
        // The Worker really wrote both the newest entry and the snapshot's
        // coverage, so either bounds what a client can legitimately hold.
        let head = self.head(&self.layout.ev(&snapshot.sid)?).await?.unwrap_or(START);
        let covered = snapshot.snapshot_event_id.map_or(START, stream_id);
        Ok((at <= head.max(covered)).then_some(head))
    }

    fn cursor(&self, sid: &Sid, tid: &Tid, id: StreamId) -> TaskCursor {
        position::cursor(&self.config.backend, sid, tid, id)
    }
}

enum Stored {
    Live(TaskSnapshot),
    Archived(FinalTaskRecord),
}

fn is_terminal(status: TaskStatus) -> bool {
    matches!(
        status,
        TaskStatus::Completed | TaskStatus::Failed | TaskStatus::Canceled | TaskStatus::Rejected
    )
}

fn live_view(snapshot: TaskSnapshot) -> TaskView {
    TaskView {
        scope: snapshot.scope,
        sid: snapshot.sid,
        tid: snapshot.tid,
        content: snapshot.content,
        covered_through: snapshot.snapshot_event_id,
    }
}

fn archived_view(record: FinalTaskRecord) -> TaskView {
    let status = match record.status {
        TerminalStatus::Completed => TaskStatus::Completed,
        TerminalStatus::Failed => TaskStatus::Failed,
        TerminalStatus::Canceled => TaskStatus::Canceled,
        TerminalStatus::Rejected => TaskStatus::Rejected,
    };
    let result_ref = match record.result {
        Payload::Ref(reference) => Some(reference),
        Payload::Inline(_) => None,
    };
    TaskView {
        scope: record.scope,
        sid: record.sid,
        tid: record.tid,
        content: TaskContent {
            status,
            interactions: Vec::new(),
            snapshot: record.snapshot,
            result_ref,
        },
        covered_through: None,
    }
}

fn stored_view(stored: Stored) -> TaskView {
    match stored {
        Stored::Live(snapshot) => live_view(snapshot),
        Stored::Archived(record) => archived_view(record),
    }
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid {
        code: code.to_owned(),
    }
}

fn in_session(sid: &Sid, tid: &Tid) -> bool {
    tid.0.strip_prefix(&sid.0).is_some_and(|rest| rest.starts_with('.'))
}

#[async_trait]
impl TaskReaderPort for StoreTaskReader {
    async fn load_snapshot(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<TaskSnapshot>, StoreError> {
        Ok(self.shared.snapshot(tid).await?.filter(|snapshot| &snapshot.scope == scope))
    }

    async fn restore(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<RestoredTask>, StoreError> {
        Ok(match self.shared.stored(scope, tid).await? {
            None => None,
            Some(Stored::Archived(record)) => Some(RestoredTask::Final(archived_view(record))),
            Some(Stored::Live(snapshot)) if is_terminal(snapshot.content.status) => {
                Some(RestoredTask::Final(live_view(snapshot)))
            }
            Some(Stored::Live(snapshot)) => {
                // No coverage yet: start before the first entry, so the first
                // events of the task are never skipped.
                let at = snapshot.snapshot_event_id.map_or(START, stream_id);
                let cursor = self.shared.cursor(&snapshot.sid, &snapshot.tid, at);
                Some(RestoredTask::Live {
                    view: live_view(snapshot),
                    cursor,
                })
            }
        })
    }

    async fn validate_resume(
        &self,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<ResumeCheck, StoreError> {
        if !in_session(&cursor.sid, &cursor.tid) {
            return Ok(ResumeCheck::InvalidScope);
        }
        let Some(at) = position::parse(&self.shared.config.backend, &cursor.token) else {
            return Ok(ResumeCheck::Gap);
        };
        let Some(snapshot) = self.shared.snapshot(&cursor.tid).await? else {
            // Expired: only the final record is left, nothing to resume.
            return Ok(ResumeCheck::Gap);
        };
        if &snapshot.scope != scope || snapshot.sid != cursor.sid {
            return Ok(ResumeCheck::InvalidScope);
        }
        Ok(match self.shared.followable(&snapshot, at).await? {
            Some(_) => ResumeCheck::Valid,
            None => ResumeCheck::Gap,
        })
    }

    fn follow(
        &self,
        scope: TaskScope,
        after: TaskCursor,
    ) -> BoxStream<'static, Result<FollowItem, StoreError>> {
        let follow = Follow {
            shared: self.shared.clone(),
            scope,
            cursor: after,
            pending: VecDeque::new(),
            state: State::Start,
        };
        futures::stream::unfold(follow, Follow::step).boxed()
    }

    async fn list(
        &self,
        scope: &TaskScope,
        filter: TaskFilter,
        page: PageRequest,
    ) -> Result<Page<TaskView>, StoreError> {
        let shared = &self.shared;
        let Some(sid) = &filter.sid else {
            return Err(invalid("session_filter_required"));
        };
        let after = page.after.as_ref().map(page::decode).transpose()?;
        let index = shared
            .zset
            .range(
                &shared.layout.session_tasks(sid)?,
                &ZSetRange {
                    min: Bound::Unbounded,
                    max: Bound::Unbounded,
                    after,
                    limit: page.limit,
                },
            )
            .await
            .map_err(|e| from_read(&e))?;
        let next = (index.len() >= page.limit.get() as usize)
            .then(|| index.last().map(page::encode))
            .flatten();
        let reads: Vec<_> = index
            .iter()
            .map(|entry| read_stored(shared.clone(), scope.clone(), Tid(entry.member.clone())))
            .collect();
        let stored: Vec<_> = futures::stream::iter(reads)
            .buffered(LIST_READ_CONCURRENCY)
            .collect()
            .await;
        let mut items = Vec::with_capacity(stored.len());
        for task in stored {
            // Absent: another scope's task, or expired with no final record.
            let Some(view) = task?.map(stored_view) else {
                continue;
            };
            if filter.statuses.is_empty() || filter.statuses.contains(&view.content.status) {
                items.push(view);
            }
        }
        Ok(Page { items, next })
    }
}

async fn read_stored(
    shared: Arc<Shared>,
    scope: TaskScope,
    tid: Tid,
) -> Result<Option<Stored>, StoreError> {
    shared.stored(&scope, &tid).await
}

struct Follow {
    shared: Arc<Shared>,
    scope: TaskScope,
    cursor: TaskCursor,
    pending: VecDeque<OutputEvent>,
    state: State,
}

enum State {
    Start,
    /// Reading what the shared reader already passed: `(from, upto]`.
    CatchUp {
        subscription: Subscription,
        key: String,
        from: StreamId,
        upto: StreamId,
    },
    /// Receiving from the shared reader; ids at or below `floor` were delivered.
    Live {
        subscription: Subscription,
        floor: StreamId,
    },
    Done,
}

type Step = Option<(Result<FollowItem, StoreError>, Follow)>;

impl Follow {
    async fn step(mut self) -> Step {
        loop {
            if let Some(event) = self.pending.pop_front() {
                let next = self.shared.cursor(&self.cursor.sid, &self.cursor.tid, stream_id(event.id));
                let item = FollowItem::Events {
                    items: vec![event],
                    next,
                };
                return Some((Ok(item), self));
            }
            match std::mem::replace(&mut self.state, State::Done) {
                State::Done => return None,
                State::Start => match self.start().await {
                    Ok(Some(state)) => self.state = state,
                    Ok(None) => return Some((Ok(FollowItem::Gap), self)),
                    Err(error) => return Some((Err(error), self)),
                },
                State::CatchUp {
                    subscription,
                    key,
                    from,
                    upto,
                } => {
                    let limit = self.shared.config.read_batch;
                    let page = self
                        .shared
                        .stream
                        .range(&key, Bound::Excluded(from), Bound::Included(upto), limit, false)
                        .await;
                    let entries = match page {
                        Ok(page) => page.entries,
                        Err(error) => return Some((Err(from_read(&error)), self)),
                    };
                    self.state = match entries.last().map(|entry| entry.id) {
                        Some(last) if entries.len() >= limit.get() as usize => State::CatchUp {
                            subscription,
                            key,
                            from: last,
                            upto,
                        },
                        _ => State::Live {
                            subscription,
                            floor: upto,
                        },
                    };
                    self.take(&entries, None);
                }
                State::Live {
                    mut subscription,
                    floor,
                } => match subscription.rx.recv().await {
                    Ok(Message::Entries(entries)) => {
                        let last = entries.last().map_or(floor, |entry| entry.id);
                        self.take(&entries, Some(floor));
                        self.state = State::Live {
                            subscription,
                            floor: floor.max(last),
                        };
                    }
                    Ok(Message::Failed(error)) => return Some((Err(error), self)),
                    Err(RecvError::Lagged(_)) => {
                        return Some((Err(not_applied(SUBSCRIBER_LAGGED)), self));
                    }
                    Err(RecvError::Closed) => {
                        return Some((Err(not_applied("session_reader_closed")), self));
                    }
                },
            }
        }
    }

    /// Checks the cursor and the task, then joins the session's reader.
    /// `Ok(None)` = the cursor cannot be followed (gap).
    async fn start(&self) -> Result<Option<State>, StoreError> {
        let shared = &self.shared;
        if !in_session(&self.cursor.sid, &self.cursor.tid) {
            return Err(invalid("cursor_task_not_in_session"));
        }
        let Some(after) = position::parse(&shared.config.backend, &self.cursor.token) else {
            return Ok(None);
        };
        // One task read per subscription (never per event): the scope check,
        // and whether the position can still be followed. The task may have
        // advanced or finished since the caller restored or validated it.
        let head = match shared.snapshot(&self.cursor.tid).await? {
            Some(snapshot) if snapshot.scope == self.scope && snapshot.sid == self.cursor.sid => {
                match shared.followable(&snapshot, after).await? {
                    Some(head) => head,
                    None => return Ok(None),
                }
            }
            Some(_) => return Err(invalid("cursor_scope_mismatch")),
            None => return Ok(None),
        };
        let key = shared.layout.ev(&self.cursor.sid)?;
        let subscription = shared.readers.join(&key, head);
        let upto = subscription.joined_at;
        Ok(Some(if after < upto {
            State::CatchUp {
                subscription,
                key,
                from: after,
                upto,
            }
        } else {
            State::Live {
                subscription,
                floor: after,
            }
        }))
    }

    /// Queues this task's events from `entries`, skipping ids at or below `floor`.
    fn take(&mut self, entries: &[StreamEntry], floor: Option<StreamId>) {
        for entry in entries {
            if floor.is_some_and(|floor| entry.id <= floor) {
                continue;
            }
            if ev_entry_tid(entry).as_ref() != Some(&self.cursor.tid) {
                continue;
            }
            match decode_ev_entry(entry) {
                Ok(event) => self.pending.push_back(event),
                // Not readable by this version: move on, don't forward.
                Err(error) => tracing::warn!(?error, id = ?entry.id, "skipping undecodable ev entry"),
            }
        }
    }
}

fn not_applied(code: &str) -> StoreError {
    StoreError::NotApplied {
        code: code.to_owned(),
    }
}
