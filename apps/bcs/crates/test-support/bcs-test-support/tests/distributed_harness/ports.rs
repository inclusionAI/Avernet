// Test-only scripted doubles. Unscripted calls fail; no backend is implemented.
use super::*;

#[async_trait]
impl InboxProducerPort for Script {
    async fn admit(&self, request: AdmitRequest) -> Result<AdmitOutcome, StoreError> {
        self.call("InboxProducerPort::admit", (request,))
    }

    async fn append_stream(&self, request: AppendAgentEvent) -> Result<InboxPos, StoreError> {
        self.call("InboxProducerPort::append_stream", (request,))
    }
}

#[async_trait]
impl ShardFeedPort for Script {
    async fn watch(&mut self, sid: Sid, after: Option<InboxPos>) -> Result<(), StoreError> {
        self.call("ShardFeedPort::watch", (sid, after))
    }

    async fn unwatch(&mut self, sid: &Sid) -> Result<(), StoreError> {
        self.call("ShardFeedPort::unwatch", (sid,))
    }

    async fn next(
        &mut self,
        limits: ReadLimits,
        timeout: Duration,
    ) -> Result<FeedBatch, StoreError> {
        self.call("ShardFeedPort::next", (limits, timeout))
    }
}

#[async_trait]
impl InboxConsumerPort for Script {
    async fn open(&self, shard: ShardId) -> Result<Box<dyn ShardFeedPort>, StoreError> {
        self.call("InboxConsumerPort::open", (shard,))
    }

    async fn has_unconsumed(
        &self,
        sid: &Sid,
        after: Option<&InboxPos>,
    ) -> Result<bool, StoreError> {
        self.call("InboxConsumerPort::has_unconsumed", (sid, after))
    }

    async fn confirm_committed(
        &self,
        delivery: &DeliveryReceipt,
        proof: &CommitReceipt,
    ) -> Result<(), StoreError> {
        self.call("InboxConsumerPort::confirm_committed", (delivery, proof))
    }

    async fn trim_committed(&self, proof: &CommitReceipt) -> Result<(), StoreError> {
        self.call("InboxConsumerPort::trim_committed", (proof,))
    }

    async fn enumerate_pending(
        &self,
        shard: ShardId,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError> {
        self.call("InboxConsumerPort::enumerate_pending", (shard, page))
    }
}

#[async_trait]
impl InboxSchedulingPort for Script {
    async fn observe(&self, owner: &SessionOwnership) -> Result<ScheduleObservation, StoreError> {
        self.call("InboxSchedulingPort::observe", (owner,))
    }

    async fn settle(&self, request: SettleSession) -> Result<SettleOutcome, StoreError> {
        self.call("InboxSchedulingPort::settle", (request,))
    }

    async fn due(
        &self,
        shard: ShardId,
        now: TimestampMillis,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError> {
        self.call("InboxSchedulingPort::due", (shard, now, page))
    }
}

#[async_trait]
impl SessionStorePort for Script {
    async fn get_spec(&self, sid: &Sid) -> Result<Option<SessionSpec>, StoreError> {
        self.call("SessionStorePort::get_spec", (sid,))
    }

    async fn acquire(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
        initial: Checkpoint,
    ) -> Result<AcquireOutcome, StoreError> {
        self.call("SessionStorePort::acquire", (sid, claim, initial))
    }

    async fn load(&self, sid: &Sid) -> Result<Option<CheckpointEnvelope>, StoreError> {
        self.call("SessionStorePort::load", (sid,))
    }

    async fn commit(&self, request: CommitRequest) -> Result<CommitOutcome, StoreError> {
        self.call("SessionStorePort::commit", (request,))
    }

    async fn resolve_commit(
        &self,
        owner: &SessionOwnership,
        commit_id: &CommitId,
        digest: &Digest,
    ) -> Result<CommitResolution, StoreError> {
        self.call(
            "SessionStorePort::resolve_commit",
            (owner, commit_id, digest),
        )
    }

    async fn record_recovery_attempt(
        &self,
        owner: &SessionOwnership,
        expected_commit_seq: CommitSeq,
        attempt: RequestId,
    ) -> Result<RecoveryUpdate, StoreError> {
        self.call(
            "SessionStorePort::record_recovery_attempt",
            (owner, expected_commit_seq, attempt),
        )
    }
}

#[async_trait]
impl ShardLeasePort for Script {
    async fn claim(&self, request: ClaimShard) -> Result<LeaseClaim, StoreError> {
        self.call("ShardLeasePort::claim", (request,))
    }

    async fn current(&self, shard: ShardId) -> Result<Option<ShardClaim>, StoreError> {
        self.call("ShardLeasePort::current", (shard,))
    }
}

#[async_trait]
impl MembershipPort for Script {
    async fn register(
        &self,
        instance: Instance,
        role: RuntimeRole,
        ttl: Duration,
    ) -> Result<MembershipLease, StoreError> {
        self.call("MembershipPort::register", (instance, role, ttl))
    }

    async fn renew(&self, lease: &MembershipLease, ttl: Duration) -> Result<Renewal, StoreError> {
        self.call("MembershipPort::renew", (lease, ttl))
    }

    async fn set_draining(&self, lease: &MembershipLease) -> Result<Renewal, StoreError> {
        self.call("MembershipPort::set_draining", (lease,))
    }

    async fn liveness(&self, instance: &Instance) -> Result<Liveness, StoreError> {
        self.call("MembershipPort::liveness", (instance,))
    }

    async fn members(
        &self,
        role: RuntimeRole,
        page: PageRequest,
    ) -> Result<Page<Instance>, StoreError> {
        self.call("MembershipPort::members", (role, page))
    }
}

#[async_trait]
impl ResultStorePort for Script {
    async fn save_final(
        &self,
        source: EffectSource,
        record: FinalTaskRecord,
    ) -> Result<SavedResult, StoreError> {
        self.call("ResultStorePort::save_final", (source, record))
    }

    async fn get_final(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<FinalTaskRecord>, StoreError> {
        self.call("ResultStorePort::get_final", (scope, tid))
    }
}

#[async_trait]
impl BlobStorePort for Script {
    async fn put_immutable(
        &self,
        identity: BlobRef,
        bytes: Vec<u8>,
    ) -> Result<IdempotentWrite, StoreError> {
        self.call("BlobStorePort::put_immutable", (identity, bytes))
    }

    async fn get(&self, reference: &BlobRef) -> Result<Option<Vec<u8>>, StoreError> {
        self.call("BlobStorePort::get", (reference,))
    }
}

#[async_trait]
impl TaskPublisherPort for Script {
    async fn append(&self, request: AppendCommittedEvents) -> Result<AppendOutcome, StoreError> {
        self.call("TaskPublisherPort::append", (request,))
    }

    async fn publish(
        &self,
        request: PublishCommittedSnapshot,
    ) -> Result<PublishOutcome, StoreError> {
        self.call("TaskPublisherPort::publish", (request,))
    }

    async fn trim(&self, request: RetentionRequest) -> Result<TrimOutcome, StoreError> {
        self.call("TaskPublisherPort::trim", (request,))
    }
}

#[async_trait]
impl TaskReaderPort for Script {
    async fn restore(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<RestoredTask>, StoreError> {
        self.call("TaskReaderPort::restore", (scope, tid))
    }

    async fn validate_resume(
        &self,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<ResumeCheck, StoreError> {
        self.call("TaskReaderPort::validate_resume", (scope, cursor))
    }

    fn follow(
        &self,
        scope: TaskScope,
        after: TaskCursor,
    ) -> BoxStream<'static, Result<FollowItem, StoreError>> {
        self.call("TaskReaderPort::follow", (scope, after))
    }

    async fn list(
        &self,
        scope: &TaskScope,
        filter: TaskFilter,
        page: PageRequest,
    ) -> Result<Page<TaskView>, StoreError> {
        self.call("TaskReaderPort::list", (scope, filter, page))
    }
}

#[async_trait]
impl DispatchControlPort for Script {
    async fn create(
        &self,
        request: CreateCommittedDispatch,
    ) -> Result<IdempotentWrite, StoreError> {
        self.call("DispatchControlPort::create", (request,))
    }

    async fn request_cancel(
        &self,
        request: CommittedCancelIntent,
    ) -> Result<ControlOutcome, StoreError> {
        self.call("DispatchControlPort::request_cancel", (request,))
    }

    async fn push_input(
        &self,
        request: CommittedDispatchInput,
    ) -> Result<ControlOutcome, StoreError> {
        self.call("DispatchControlPort::push_input", (request,))
    }

    async fn notify(&self, pool: &PoolId, did: &Did) -> Result<(), StoreError> {
        self.call("DispatchControlPort::notify", (pool, did))
    }

    async fn get(&self, did: &Did) -> Result<Option<DispatchRecord>, StoreError> {
        self.call("DispatchControlPort::get", (did,))
    }

    async fn scan_due(
        &self,
        shard: ShardId,
        now: TimestampMillis,
        page: PageRequest,
    ) -> Result<Page<Did>, StoreError> {
        self.call("DispatchControlPort::scan_due", (shard, now, page))
    }

    async fn reschedule(
        &self,
        did: &Did,
        expected: Revision,
        at: TimestampMillis,
    ) -> Result<RecheckOutcome, StoreError> {
        self.call("DispatchControlPort::reschedule", (did, expected, at))
    }
}

#[async_trait]
impl DispatchQueuePort for Script {
    async fn claim(&self, request: DispatchClaimRequest) -> Result<ClaimOutcome, StoreError> {
        self.call("DispatchQueuePort::claim", (request,))
    }

    async fn resume_owned(&self, request: ResumeOwnedDispatch) -> Result<ClaimOutcome, StoreError> {
        self.call("DispatchQueuePort::resume_owned", (request,))
    }

    async fn notifications(
        &self,
        pool: PoolId,
        me: Instance,
    ) -> Result<Box<dyn CommandFeedPort>, StoreError> {
        self.call("DispatchQueuePort::notifications", (pool, me))
    }
}

#[async_trait]
impl CommandFeedPort for Script {
    async fn receive(
        &mut self,
        max: NonZeroU32,
        timeout: Duration,
    ) -> Result<Vec<CommandDelivery>, StoreError> {
        self.call("CommandFeedPort::receive", (max, timeout))
    }

    async fn ack(&mut self, delivery: &CommandDelivery) -> Result<(), StoreError> {
        self.call("CommandFeedPort::ack", (delivery,))
    }
}

#[async_trait]
impl DistAgentClientPort for Script {
    async fn start(&self, target: &AgentBinding, request: StableSendRequest) -> SendOutcome {
        self.call("DistAgentClientPort::start", (target, request))
    }

    async fn send_input(
        &self,
        target: &AgentBinding,
        remote: &RemoteTaskRef,
        request: StableSendRequest,
    ) -> SendOutcome {
        self.call("DistAgentClientPort::send_input", (target, remote, request))
    }

    async fn query(&self, target: &AgentBinding, query: RemoteQuery) -> QueryOutcome {
        self.call("DistAgentClientPort::query", (target, query))
    }

    async fn subscribe(
        &self,
        target: &AgentBinding,
        remote: &RemoteTaskRef,
        after: Option<ReplayToken>,
    ) -> Result<RemoteEventStream, AgentReadError> {
        self.call("DistAgentClientPort::subscribe", (target, remote, after))
    }

    async fn cancel(
        &self,
        target: &AgentBinding,
        remote: &RemoteTaskRef,
        request_id: RequestId,
    ) -> CancelOutcome {
        self.call("DistAgentClientPort::cancel", (target, remote, request_id))
    }
}

#[async_trait]
impl DistGroupDirectoryPort for Script {
    async fn resolve(
        &self,
        scope: &TaskScope,
        group: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        self.call("DistGroupDirectoryPort::resolve", (scope, group))
    }

    async fn load_workflow(
        &self,
        scope: &TaskScope,
        reference: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        self.call("DistGroupDirectoryPort::load_workflow", (scope, reference))
    }
}

#[async_trait]
impl DistRuntimeControlPort for Script {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        self.call("DistRuntimeControlPort::snapshot", ())
    }

    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        self.call("DistRuntimeControlPort::drain", (grace,))
    }
}

#[async_trait]
impl DistGatewayService for Script {
    async fn submit(
        &self,
        caller: &GatewayCaller,
        request: SubmitInput,
    ) -> Result<AdmissionReceipt, GatewayError> {
        self.call("DistGatewayService::submit", (caller, request))
    }

    async fn get_task(&self, caller: &GatewayCaller, tid: &Tid) -> Result<TaskView, GatewayError> {
        self.call("DistGatewayService::get_task", (caller, tid))
    }

    async fn subscribe(
        &self,
        caller: &GatewayCaller,
        request: SubscribeTask,
    ) -> Result<TaskSubscription, GatewayError> {
        self.call("DistGatewayService::subscribe", (caller, request))
    }

    async fn cancel(
        &self,
        caller: &GatewayCaller,
        request: CancelTask,
    ) -> Result<AdmissionReceipt, GatewayError> {
        self.call("DistGatewayService::cancel", (caller, request))
    }

    async fn list_tasks(
        &self,
        caller: &GatewayCaller,
        request: ListTasks,
    ) -> Result<TaskPage, GatewayError> {
        self.call("DistGatewayService::list_tasks", (caller, request))
    }
}

#[async_trait]
impl DistWorkerRuntimeService for Script {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        self.call("DistWorkerRuntimeService::snapshot", ())
    }

    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        self.call("DistWorkerRuntimeService::drain", (grace,))
    }
}

#[async_trait]
impl DistConnectorRuntimeService for Script {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        self.call("DistConnectorRuntimeService::snapshot", ())
    }

    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        self.call("DistConnectorRuntimeService::drain", (grace,))
    }
}

#[async_trait]
impl DistGatewayCoreService for Script {
    async fn admit(
        &self,
        scope: &TaskScope,
        command: AdmitTaskCommand,
    ) -> Result<CoreAdmission, DistGatewayCoreError> {
        self.call("DistGatewayCoreService::admit", (scope, command))
    }

    async fn restore_task(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<RestoredTaskCut, DistGatewayCoreError> {
        self.call("DistGatewayCoreService::restore_task", (scope, tid))
    }

    async fn validate_resume(
        &self,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<CoreResumeCheck, DistGatewayCoreError> {
        self.call("DistGatewayCoreService::validate_resume", (scope, cursor))
    }

    fn follow_task(
        &self,
        scope: TaskScope,
        cursor: TaskCursor,
    ) -> BoxStream<'static, Result<CoreFollowItem, DistGatewayCoreError>> {
        self.call("DistGatewayCoreService::follow_task", (scope, cursor))
    }

    async fn list_tasks(
        &self,
        scope: &TaskScope,
        query: CoreTaskQuery,
    ) -> Result<CoreTaskPage, DistGatewayCoreError> {
        self.call("DistGatewayCoreService::list_tasks", (scope, query))
    }
}
