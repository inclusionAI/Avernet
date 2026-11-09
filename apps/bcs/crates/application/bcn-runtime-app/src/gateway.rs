//! Admission, authorization and replay use durable original task metadata.
use crate::*;
use async_trait::async_trait;
use bcn_runtime_api::{application::*, core::SessionRunner};
use std::{collections::VecDeque, sync::Arc, time::Duration};
pub struct DefaultGateway {
    ports: RuntimePorts,
    runners: Vec<Arc<dyn SessionRunner>>,
    catalog: Arc<dyn AgentCatalog>,
    policies: Vec<Arc<dyn AccessPolicy>>,
    clock: Arc<dyn Clock>,
    enabled: bool,
}
impl DefaultGateway {
    /// Admission is explicit; bootstrap must pass false unless the new namespace is enabled.
    pub fn new(
        ports: RuntimePorts,
        runners: Vec<Arc<dyn SessionRunner>>,
        catalog: Arc<dyn AgentCatalog>,
        policies: Vec<Arc<dyn AccessPolicy>>,
        clock: Arc<dyn Clock>,
        enabled: bool,
    ) -> RuntimeResult<Self> {
        if policies.is_empty() {
            return Err(RuntimeError::InvalidInput(
                "access policy registration required".into(),
            ));
        }
        Ok(Self {
            ports,
            runners,
            catalog,
            policies,
            clock,
            enabled,
        })
    }
    async fn authorize(
        &self,
        ctx: &CallerContext,
        action: AccessAction,
        target: &ExecutionTarget,
        session: Option<&SessionId>,
        task: Option<&TaskId>,
    ) -> RuntimeResult<()> {
        if ctx.principal.trim().is_empty()
            || target_scope(target) != &ctx.scope
            || session.is_some_and(|s| s.scope != ctx.scope)
            || task.is_some_and(|t| t.scope != ctx.scope)
        {
            return Err(RuntimeError::AccessDenied);
        }
        for policy in &self.policies {
            policy.authorize(ctx, action, target, session, task).await?;
        }
        Ok(())
    }
    async fn task(
        &self,
        ctx: &CallerContext,
        id: &TaskId,
        action: AccessAction,
    ) -> RuntimeResult<TaskView> {
        let task = self.ports.queries.get_task(ctx, id).await?;
        self.authorize(ctx, action, &task.target, Some(&task.session_id), Some(id))
            .await?;
        Ok(task)
    }
    async fn reply(
        &self,
        ctx: CallerContext,
        request: SubmitRequest,
        id: TaskId,
    ) -> RuntimeResult<TaskView> {
        let task = self.task(&ctx, &id, AccessAction::HumanReply).await?;
        if request.target != task.target
            || request
                .message
                .context_id
                .as_ref()
                .is_some_and(|s| s != &task.session_id)
        {
            return Err(RuntimeError::AccessDenied);
        }
        let event_id = EventId::new(
            ctx.scope.clone(),
            hash("reply", &(&ctx.principal, &id, &request.message.message_id))?,
        );
        let mut message = request.message;
        message.context_id = Some(task.session_id.clone());
        let mut stable_caller = ctx.clone();
        stable_caller.trace_id = None;
        if let Some(prior) = self
            .ports
            .inbox
            .find_input(&task.session_id, &event_id)
            .await?
        {
            match prior.kind {
                SessionInputKind::HumanReply {
                    task_id,
                    interaction_id,
                    caller,
                    message: original,
                } if task_id == id
                    && caller == stable_caller
                    && original == message
                    && request
                        .interaction_id
                        .as_ref()
                        .is_none_or(|i| i == &interaction_id) =>
                {
                    return Ok(task);
                }
                _ => return Err(RuntimeError::Conflict("reply identity reused".into())),
            }
        }
        let cp = self
            .ports
            .sessions
            .load(&task.session_id)
            .await?
            .ok_or(RuntimeError::NotFound)?;
        let active = cp
            .state
            .current_task
            .as_ref()
            .filter(|t| t.task_id == id && !t.cancel_requested)
            .ok_or_else(|| RuntimeError::Conflict("task is not waiting for input".into()))?;
        let choices: Vec<_> = active
            .nodes
            .values()
            .filter_map(|n| n.interaction_id.clone())
            .collect();
        let interaction_id = match request.interaction_id {
            Some(i) if choices.contains(&i) => i,
            Some(_) => {
                return Err(RuntimeError::Conflict(
                    "interaction is not active on original task".into(),
                ));
            }
            None if choices.len() == 1 => choices[0].clone(),
            _ => {
                return Err(RuntimeError::InvalidInput(
                    "explicit interaction_id required for ambiguous or absent interaction".into(),
                ));
            }
        };
        self.ports
            .inbox
            .append_and_wake(
                &task.session_id,
                SessionInput {
                    event_id,
                    at_ms: self.clock.now_ms(),
                    kind: SessionInputKind::HumanReply {
                        task_id: id,
                        interaction_id,
                        caller: stable_caller,
                        message,
                    },
                },
            )
            .await?;
        Ok(task)
    }
}
#[async_trait]
impl GatewayService for DefaultGateway {
    async fn submit(&self, ctx: CallerContext, request: SubmitRequest) -> RuntimeResult<TaskView> {
        if request.message.message_id.scope != ctx.scope
            || request
                .interaction_id
                .as_ref()
                .is_some_and(|i| i.scope != ctx.scope)
        {
            return Err(RuntimeError::AccessDenied);
        }
        if size(&request.message.parts)? > MAX_CONTENT_BYTES
            || size(&request)? > MAX_CONTENT_BYTES + 4096
        {
            return Err(RuntimeError::CapacityExceeded);
        }
        if request.message.parts.iter().any(|part| !matches!(part, MessagePart::Text { .. })) {
            return Err(RuntimeError::Unsupported("MVP accepts text/plain only".into()));
        }
        if let Some(task_id) = request.message.task_id.clone() {
            return self.reply(ctx, request, task_id).await;
        }
        if !self.enabled {
            return Err(RuntimeError::Unavailable(
                "new runtime admission disabled".into(),
            ));
        }
        if request.interaction_id.is_some() {
            return Err(RuntimeError::InvalidInput(
                "interaction requires original task".into(),
            ));
        }
        self.authorize(
            &ctx,
            AccessAction::Submit,
            &request.target,
            request.message.context_id.as_ref(),
            None,
        )
        .await?;
        let identity = hash(
            "admission",
            &(&ctx.principal, &request.target, &request.message.message_id),
        )?;
        let admission_id = AdmissionId::new(ctx.scope.clone(), identity.clone());
        let request_digest = hash("request", &(&ctx.principal, &request))?;
        if let Some(receipt) = self.ports.admissions.get_receipt(&admission_id).await? {
            if receipt.request_digest != request_digest || receipt.caller.principal != ctx.principal
            {
                return Err(RuntimeError::Conflict(
                    "message identity reused with different content/context".into(),
                ));
            }
            return self
                .task(&ctx, &receipt.task.task_id, AccessAction::Submit)
                .await;
        }
        let session_id = request
            .message
            .context_id
            .clone()
            .unwrap_or_else(|| SessionId::new(ctx.scope.clone(), identity.clone()));
        let spec = if let Some(spec) = self.ports.sessions.load_spec(&session_id).await? {
            let own = self
                .ports
                .queries
                .list_tasks(
                    &ctx,
                    TaskQuery {
                        target: Some(request.target.clone()),
                        session_id: Some(session_id.clone()),
                        cursor: None,
                        limit: 1,
                    },
                )
                .await?;
            if own.items.is_empty() || spec.target != request.target {
                return Err(RuntimeError::AccessDenied);
            }
            if self
                .ports
                .sessions
                .load(&session_id)
                .await?
                .is_some_and(|cp| cp.state.context_blocked)
            {
                return Err(RuntimeError::ContextBlocked);
            }
            spec
        } else {
            self.catalog
                .resolve(&ctx, &request.target, &session_id)
                .await?
        };
        if spec.session_id != session_id || spec.target != request.target {
            return Err(RuntimeError::AccessDenied);
        }
        runner(&self.runners, spec.kind)?.validate(&spec)?;
        if size(&spec)? > MAX_SPEC_BYTES {
            return Err(RuntimeError::CapacityExceeded);
        }
        let now = self.clock.now_ms();
        let task_id = TaskId::new(ctx.scope.clone(), identity.clone());
        let task = TaskView {
            active_interactions: vec![],
            task_id: task_id.clone(),
            session_id: session_id.clone(),
            target: request.target,
            status: TaskStatus::Submitted,
            output: vec![],
            error: None,
            created_at_ms: now,
            updated_at_ms: now,
        };
        let input = SessionInput {
            event_id: EventId::new(ctx.scope.clone(), hash("submit", &identity)?),
            at_ms: now,
            kind: SessionInputKind::Submit {
                task: QueuedTask {
                    task_id,
                    message: request.message,
                    admitted_at_ms: now,
                    terminal_intent: None,
                },
            },
        };
        let receipt = self
            .ports
            .admissions
            .admit(AdmissionRequest {
                admission_id,
                caller: ctx.clone(),
                request_digest,
                spec,
                task,
                input,
            })
            .await?;
        self.ports
            .queries
            .get_task(&ctx, &receipt.task.task_id)
            .await
    }
    async fn get(&self, ctx: CallerContext, id: TaskId) -> RuntimeResult<TaskView> {
        self.task(&ctx, &id, AccessAction::Get).await
    }
    async fn list(&self, ctx: CallerContext, query: TaskQuery) -> RuntimeResult<TaskPage> {
        if ctx.principal.trim().is_empty()
            || query
                .session_id
                .as_ref()
                .is_some_and(|s| s.scope != ctx.scope)
        {
            return Err(RuntimeError::AccessDenied);
        }
        if query.target.as_ref().is_some_and(|target|target_scope(target)!=&ctx.scope) {return Err(RuntimeError::AccessDenied)}
        for policy in &self.policies {policy.authorize_list(&ctx,&query).await?;}
        let page = self.ports.queries.list_tasks(&ctx, query).await?;
        for t in &page.items {
            self.authorize(
                &ctx,
                AccessAction::List,
                &t.target,
                Some(&t.session_id),
                Some(&t.task_id),
            )
            .await?;
        }
        Ok(page)
    }
    async fn cancel(&self, ctx: CallerContext, command: CancelCommand) -> RuntimeResult<TaskView> {
        let task = self
            .task(&ctx, &command.task_id, AccessAction::Cancel)
            .await?;
        if command.operation_id.scope != ctx.scope {
            return Err(RuntimeError::AccessDenied);
        }
        let event_id = EventId::new(
            ctx.scope.clone(),
            hash("cancel", &(&ctx.principal, &command.operation_id))?,
        );
        if let Some(prior) = self
            .ports
            .inbox
            .find_input(&task.session_id, &event_id)
            .await?
        {
            if prior.kind != (SessionInputKind::Cancel { command }) {
                return Err(RuntimeError::Conflict("cancel identity reused".into()));
            }
            return Ok(task);
        }
        if !task.status.is_terminal() {
            self.ports
                .inbox
                .append_and_wake(
                    &task.session_id,
                    SessionInput {
                        event_id,
                        at_ms: self.clock.now_ms(),
                        kind: SessionInputKind::Cancel { command },
                    },
                )
                .await?;
        }
        Ok(task)
    }
    async fn subscribe(
        &self,
        ctx: CallerContext,
        id: TaskId,
        cursor: Option<EventCursor>,
    ) -> RuntimeResult<EventStream> {
        let task = self.task(&ctx, &id, AccessAction::Subscribe).await?;
        if task.status.is_terminal() {
            return Err(RuntimeError::Conflict(
                "terminal task must be queried".into(),
            ));
        }
        let page = self
            .ports
            .events
            .read_after(&id, cursor.clone(), 100)
            .await?;
        let mut queue = VecDeque::new();
        let first = cursor.is_none() || page.cursor_expired;
        let mut next = cursor;
        if first {
            next = Some(page.snapshot.cursor.clone());
            queue.push_back(Ok(TaskEvent {
                id: EventId::new(
                    ctx.scope.clone(),
                    hash("snapshot", &(&id, &page.snapshot.cursor))?,
                ),
                task_id: id.clone(),
                cursor: page.snapshot.cursor.clone(),
                kind: TaskEventKind::Reset {
                    snapshot: page.snapshot.task.clone(),
                },
            }));
        } else {
            for e in page.events {
                next = Some(e.cursor.clone());
                queue.push_back(Ok(e));
            }
        }
        let ports = self.ports.clone();
        let policies = self.policies.clone();
        Ok(Box::pin(futures::stream::unfold(
            (queue, next, false),
            move |(mut queue, mut cursor, mut done)| {
                let ports = ports.clone();
                let ctx = ctx.clone();
                let id = id.clone();
                let policies = policies.clone();
                async move {
                    loop {
                        if let Some(event) = queue.pop_front() {
                            return Some((event, (queue, cursor, done)));
                        }
                        if done {
                            return None;
                        }
                        tokio::time::sleep(Duration::from_millis(200)).await;
                        let fetch = async {
                            let task = ports.queries.get_task(&ctx, &id).await?;
                            for policy in &policies {
                                policy
                                    .authorize(
                                        &ctx,
                                        AccessAction::Subscribe,
                                        &task.target,
                                        Some(&task.session_id),
                                        Some(&id),
                                    )
                                    .await?;
                            }
                            ports.events.read_after(&id, cursor.clone(), 100).await
                        }
                        .await;
                        match fetch {
                            Ok(page) => {
                                if page.cursor_expired {
                                    cursor = Some(page.snapshot.cursor.clone());
                                    queue.push_back(Ok(TaskEvent {
                                        id: EventId::new(id.scope.clone(), "snapshot"),
                                        task_id: id.clone(),
                                        cursor: page.snapshot.cursor.clone(),
                                        kind: TaskEventKind::Reset {
                                            snapshot: page.snapshot.task.clone(),
                                        },
                                    }));
                                } else {
                                    for e in page.events {
                                        cursor = Some(e.cursor.clone());
                                        queue.push_back(Ok(e));
                                    }
                                }
                                done = page.snapshot.task.status.is_terminal()
                                    && cursor.as_ref() == Some(&page.snapshot.cursor);
                            }
                            Err(RuntimeError::Conflict(_)) => continue,
                            Err(e) => {
                                done = true;
                                queue.push_back(Err(e));
                            }
                        }
                    }
                }
            },
        )))
    }
}
