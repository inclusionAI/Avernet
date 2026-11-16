use super::*;
pub struct EventsFault {
    pub inner: Arc<RuntimeStore>,
    pub reset: AtomicBool,
    pub delta_after_commit: AtomicBool,
    pub delta_attempts: std::sync::Mutex<Vec<TaskEvent>>,
    pub hard_after_commit: AtomicBool,
}
impl EventsFault {
    pub fn new(inner: Arc<RuntimeStore>) -> Self {
        Self {
            inner,
            reset: AtomicBool::new(false),
            delta_after_commit: AtomicBool::new(false),
            delta_attempts: std::sync::Mutex::new(vec![]),
            hard_after_commit: AtomicBool::new(false),
        }
    }
}
#[async_trait]
impl TaskEventStore for EventsFault {
    async fn read_projection(&self, id: &DispatchId) -> RuntimeResult<Option<Vec<MessagePart>>> {
        self.inner.read_projection(id).await
    }
    async fn publish_hard_event(
        &self,
        f: &OwnershipFence,
        e: CommittedEventRef,
    ) -> RuntimeResult<EventCursor> {
        let r = self.inner.publish_hard_event(f, e).await?;
        if self.hard_after_commit.swap(false, Ordering::SeqCst) {
            Err(RuntimeError::Unavailable("lost publish response".into()))
        } else {
            Ok(r)
        }
    }
    async fn append(&self, f: &DispatchFence, e: TaskEvent) -> RuntimeResult<EventCursor> {
        self.delta_attempts.lock().unwrap().push(e.clone());
        let cursor = self.inner.append(f, e).await?;
        if self.delta_after_commit.swap(false, Ordering::SeqCst) {
            Err(RuntimeError::Unavailable("lost delta response".into()))
        } else { Ok(cursor) }
    }
    async fn publish_reset(
        &self,
        f: &DispatchFence,
        p: ConnectorProjection,
        e: EventId,
    ) -> RuntimeResult<EventCursor> {
        if self.reset.swap(false, Ordering::SeqCst) {
            Err(RuntimeError::Unavailable("reset offline".into()))
        } else {
            self.inner.publish_reset(f, p, e).await
        }
    }
    async fn read_after(
        &self,
        t: &TaskId,
        c: Option<EventCursor>,
        l: u32,
    ) -> RuntimeResult<EventPage> {
        self.inner.read_after(t, c, l).await
    }
    async fn read_snapshot(&self, t: &TaskId) -> RuntimeResult<Option<EventSnapshot>> {
        self.inner.read_snapshot(t).await
    }
}
pub struct LivenessFault {
    pub inner: Arc<dyn RuntimeLiveness>,
    pub fail: AtomicBool,
    pub unknown: AtomicBool,
}
#[async_trait]
impl RuntimeLiveness for LivenessFault {
    async fn register(&self, n: NodeIdentity, t: u64) -> RuntimeResult<()> {
        self.inner.register(n, t).await
    }
    async fn renew(&self, n: &NodeIdentity, t: u64) -> RuntimeResult<()> {
        if self.fail.load(Ordering::SeqCst) {
            Err(RuntimeError::Unavailable("heartbeat failed".into()))
        } else {
            self.inner.renew(n, t).await
        }
    }
    async fn inspect_batch(&self, n: &[NodeIdentity]) -> RuntimeResult<Vec<NodeObservation>> {
        if self.unknown.load(Ordering::SeqCst) {
            Ok(n.iter()
                .cloned()
                .map(|node| NodeObservation {
                    node,
                    liveness: Liveness::Unknown,
                    draining: false,
                })
                .collect())
        } else {
            self.inner.inspect_batch(n).await
        }
    }
    async fn mark_draining(&self, n: &NodeIdentity) -> RuntimeResult<()> {
        self.inner.mark_draining(n).await
    }
}
pub struct SessionFault {
    pub inner: Arc<RuntimeStore>,
    pub commit: AtomicBool,
}
#[async_trait]
impl SessionStore for SessionFault {
    async fn begin_recovery(
        &self,
        s: &SessionId,
        f: &OwnershipFence,
        t: u32,
    ) -> RuntimeResult<SessionRecovery> {
        self.inner.begin_recovery(s, f, t).await
    }
    async fn recovery_status(&self, s: &SessionId) -> RuntimeResult<Option<SessionRecovery>> {
        self.inner.recovery_status(s).await
    }
    async fn activate(
        &self,
        s: SessionSpec,
        r: RunnerState,
        f: OwnershipFence,
    ) -> RuntimeResult<SessionCheckpoint> {
        self.inner.activate(s, r, f).await
    }
    async fn load_spec(&self, s: &SessionId) -> RuntimeResult<Option<SessionSpec>> {
        self.inner.load_spec(s).await
    }
    async fn deactivate(
        &self,
        s: &SessionId,
        f: &OwnershipFence,
        r: Revision,
    ) -> RuntimeResult<()> {
        self.inner.deactivate(s, f, r).await
    }
    async fn load(&self, s: &SessionId) -> RuntimeResult<Option<SessionCheckpoint>> {
        self.inner.load(s).await
    }
    async fn commit(&self, c: SessionCommit) -> RuntimeResult<SessionCheckpoint> {
        if self.commit.swap(false, Ordering::SeqCst) {
            Err(RuntimeError::Unavailable("checkpoint interrupted".into()))
        } else {
            self.inner.commit(c).await
        }
    }
    async fn scan_active(
        &self,
        s: &ShardId,
        c: Option<String>,
        l: u32,
    ) -> RuntimeResult<Page<SessionId>> {
        SessionStore::scan_active(self.inner.as_ref(), s, c, l).await
    }
}
