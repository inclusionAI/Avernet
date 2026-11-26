use super::*;
use bcs_store_api::zset::{ZSetEntry, ZSetRange};
use futures::stream::BoxStream;
use publish::{task_status, terminal_status};
use storage::bounded_bytes;

#[derive(Serialize, Deserialize)]
struct Cursor {
    tid: Tid,
    after: Option<EventId>,
}
#[derive(Serialize, Deserialize)]
struct ListCursor {
    binding: Digest,
    after: String,
}

impl StoreTaskFeed {
    pub(super) fn cursor(
        &self,
        sid: &Sid,
        tid: &Tid,
        after: Option<EventId>,
    ) -> Result<TaskCursor, StoreError> {
        Ok(TaskCursor {
            sid: sid.clone(),
            tid: tid.clone(),
            token: ReplayToken {
                backend: BackendId("bcs-task-feed-v1".into()),
                generation: LogGeneration(
                    hex_of(&digest_fields(&[self
                        .layout
                        .namespace_identity()
                        .as_bytes()]))
                    .into(),
                ),
                value: serde_json::to_string(&Cursor {
                    tid: tid.clone(),
                    after,
                })
                .map_err(|_| invalid("output_cursor"))?,
            },
        })
    }
    pub(super) fn cursor_after(&self, cursor: &TaskCursor) -> Result<Option<EventId>, StoreError> {
        let expected = self.cursor(&cursor.sid, &cursor.tid, None)?;
        if cursor.token.backend != expected.token.backend
            || cursor.token.generation != expected.token.generation
            || cursor.token.value.len() > 1024
        {
            return Err(invalid("output_cursor"));
        }
        let value: Cursor =
            serde_json::from_str(&cursor.token.value).map_err(|_| invalid("output_cursor"))?;
        if value.tid != cursor.tid {
            return Err(invalid("output_cursor_task"));
        }
        Ok(value.after)
    }
    pub(super) async fn has_gap(
        &self,
        task: &TaskSnapshot,
        after: Option<EventId>,
    ) -> Result<bool, StoreError> {
        let retention = self
            .read::<TaskRetention>(&self.layout.task_retention(&task.tid)?)
            .await?;
        if let Some(retention) = retention {
            if retention.value.scope != task.scope || retention.value.sid != task.sid {
                return Err(invalid("output_retention_scope"));
            }
            return Ok(after.is_none_or(|id| id < retention.value.removed_through));
        }
        Ok(false)
    }
    async fn fold(
        &self,
        task: &TaskSnapshot,
        content: &mut TaskContent,
        display: &mut DisplaySnapshot,
        event: OutputEvent,
    ) -> Result<(), StoreError> {
        match event.body {
            OutputBody::Artifact {
                source,
                artifact_id,
                class,
                update,
            } => {
                if class == OutputClass::Thinking {
                    return Ok(());
                }
                let update = match update {
                    ArtifactUpdate::Upsert { content } => ArtifactUpdate::Upsert {
                        content: Payload::Inline(
                            self.payload(&content)
                                .await?,
                        ),
                    },
                    ArtifactUpdate::AppendText { delta } => ArtifactUpdate::AppendText {
                        delta: Payload::Inline(
                            self.payload(&delta)
                                .await?,
                        ),
                    },
                };
                *display = self
                    .projection
                    .fold_committed(
                        display,
                        &[ArtifactChange {
                            source,
                            artifact_id,
                            class,
                            update,
                        }],
                        &task.output_limits,
                    )
                    .map_err(|_| invalid("output_projection"))?;
            }
            OutputBody::Status {
                status,
                interactions,
            } => {
                if terminal_status(status).is_some() {
                    return Err(invalid("unarchived_terminal_event"));
                }
                content.status = status;
                content.interactions = interactions;
            }
            OutputBody::Terminal { status, result_ref } => {
                content.status = task_status(status);
                content.result_ref = Some(result_ref);
                content.interactions.clear();
            }
        }
        Ok(())
    }
}

impl StoreTaskFeed {
    async fn restore_task(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<RestoredTask>, StoreError> {
        for _ in 0..3 {
            let Some(stored) = self.task(scope, tid).await? else {
                return Ok(None);
            };
            let task = stored.value;
            self.projection
                .validate_limits(&task.output_limits)
                .map_err(|_| invalid("task_limits"))?;
            let mut display = self
                .display(&task.content.snapshot, &task.output_limits)
                .await?;
            let mut content = task.content.clone();
            let (_, head) = self.head(&task.sid).await?;
            if task.snapshot_event_id > head.completed_head {
                return Err(invalid("snapshot_ahead_of_output"));
            }
            if self.has_gap(&task, task.snapshot_event_id).await? {
                continue;
            }
            let mut after = task.snapshot_event_id;
            let started = Instant::now();
            if let Some(through) = head.completed_head {
                let mut finished = after.is_some_and(|position| position >= through);
                for _ in 0..self.config.scan_pages {
                    if finished {
                        break;
                    }
                    if started.elapsed() > self.config.scan_duration {
                        return Err(unavailable("restore_budget"));
                    }
                    let events = self
                        .page(&self.layout.task_display_ev(tid)?, after, through)
                        .await?;
                    if events.is_empty() {
                        finished = true;
                        break;
                    }
                    for event in events {
                        if event.tid != *tid {
                            return Err(invalid("display_stream_task"));
                        }
                        after = Some(event.id);
                        self.fold(&task, &mut content, &mut display, event).await?;
                    }
                    finished = after == Some(through);
                }
                if !finished {
                    return Err(unavailable("restore_page_budget"));
                }
            }
            // A concurrent logical trim invalidates the old snapshot cut even
            // if native data is still physically present.
            if self.has_gap(&task, task.snapshot_event_id).await? {
                continue;
            }
            content.snapshot = Payload::Inline(encode_json(&display)?);
            let view = TaskView {
                scope: scope.clone(),
                sid: task.sid.clone(),
                tid: tid.clone(),
                content,
                covered_through: head.completed_head,
            };
            if let Some(status) = terminal_status(view.content.status) {
                let reference = view
                    .content
                    .result_ref
                    .as_ref()
                    .ok_or_else(|| invalid("terminal_result_ref"))?;
                let record = self.final_record(&task, reference, status).await?;
                if self.display(&record.snapshot, &task.output_limits).await? != display {
                    return Err(invalid("terminal_output_incomplete"));
                }
                return Ok(Some(RestoredTask::Final(view)));
            }
            return Ok(Some(RestoredTask::Live {
                cursor: self.cursor(&task.sid, tid, head.completed_head)?,
                view,
            }));
        }
        Err(unavailable("restore_cut_changed"))
    }

    async fn check_resume(
        &self,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<ResumeCheck, StoreError> {
        let after = self.cursor_after(cursor)?;
        let Some(task) = self.task(scope, &cursor.tid).await? else {
            return Ok(ResumeCheck::InvalidScope);
        };
        if task.value.sid != cursor.sid {
            return Ok(ResumeCheck::InvalidScope);
        }
        if self.has_gap(&task.value, after).await? {
            return Ok(ResumeCheck::Gap);
        }
        let (_, head) = self.head(&cursor.sid).await?;
        if after > head.completed_head {
            return Err(invalid("output_cursor_future"));
        }
        Ok(ResumeCheck::Valid)
    }

    async fn list_tasks(
        &self,
        scope: &TaskScope,
        filter: TaskFilter,
        page: PageRequest,
    ) -> Result<Page<TaskView>, StoreError> {
        if filter.statuses.len() > 8 || page.limit.get() > 128 {
            return Err(invalid("task_list_bound"));
        }
        let binding = digest_json(
            &serde_json::json!({ "namespace": self.layout.namespace_identity(), "scope": scope, "sid":filter.sid, "statuses":filter.statuses }),
        );
        let after = match page.after {
            None => None,
            Some(token) => {
                if token.0.len() > 1024 {
                    return Err(invalid("task_list_cursor"));
                }
                let cursor: ListCursor =
                    serde_json::from_str(&token.0).map_err(|_| invalid("task_list_cursor"))?;
                if cursor.binding != binding || cursor.after.len() > 256 {
                    return Err(invalid("task_list_cursor"));
                }
                Some(ZSetEntry {
                    member: cursor.after,
                    score: 0.0,
                })
            }
        };
        let members = self
            .io(async {
                self.stores
                    .index
                    .range(
                        &self.layout.tasks(scope)?,
                        &ZSetRange {
                            min: Bound::Included(0.0),
                            max: Bound::Included(0.0),
                            after: after.clone(),
                            limit: page.limit,
                        },
                    )
                    .await
                    .map_err(|e| crate::error::from_read(&e))
            })
            .await?;
        if members.len() > page.limit.get() as usize {
            return Err(invalid("task_list_count"));
        }
        let mut scanned = after.map(|entry| entry.member);
        let mut bytes = 0usize;
        let mut items = Vec::new();
        let mut stopped = false;
        let started = Instant::now();
        for member in &members {
            if started.elapsed() >= self.config.scan_duration {
                if scanned.is_none() {
                    return Err(unavailable("task_list_budget"));
                }
                stopped = true;
                break;
            }
            let tid = Tid(member.member.clone());
            if let Some(task) = self.task(scope, &tid).await? {
                let task = task.value;
                if filter.sid.as_ref().is_none_or(|sid| *sid == task.sid)
                    && (filter.statuses.is_empty()
                        || filter.statuses.contains(&task.content.status))
                {
                    let view = TaskView {
                        scope: task.scope,
                        sid: task.sid,
                        tid,
                        content: task.content,
                        covered_through: task.snapshot_event_id,
                    };
                    let size = bounded_bytes(&view, self.config.page_bytes - 4096)?.len();
                    if size > (self.config.page_bytes - 4096).saturating_sub(bytes) {
                        stopped = true;
                        break;
                    }
                    bytes += size;
                    items.push(view);
                }
            }
            scanned = Some(member.member.clone());
        }
        let next = if stopped || members.len() == page.limit.get() as usize {
            scanned
                .map(|after| {
                    serde_json::to_string(&ListCursor { binding, after })
                        .map(PageToken)
                        .map_err(|_| invalid("task_list_cursor"))
                })
                .transpose()?
        } else {
            None
        };
        Ok(Page { items, next })
    }
}

// All reads, reference resolution and cut retries share the same monotonic
// deadline. A page-boundary check alone cannot stop a slow final read.
#[async_trait]
impl TaskReaderPort for StoreTaskFeed {
    async fn load_snapshot(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<TaskSnapshot>, StoreError> {
        let stored = self.io(self.task(scope, tid)).await?;
        if let Some(stored) = &stored {
            bounded_bytes(&stored.value, 256 * 1024)?;
            self.projection
                .validate_task_limits(&stored.value, &stored.value.output_limits)
                .map_err(|_| invalid("task_snapshot_limits"))?;
        }
        Ok(stored.map(|stored| stored.value))
    }
    async fn restore(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<RestoredTask>, StoreError> {
        tokio::time::timeout(self.config.scan_duration, self.restore_task(scope, tid))
            .await
            .map_err(|_| unavailable("restore_budget"))?
    }
    async fn validate_resume(
        &self,
        scope: &TaskScope,
        cursor: &TaskCursor,
    ) -> Result<ResumeCheck, StoreError> {
        tokio::time::timeout(self.config.scan_duration, self.check_resume(scope, cursor))
            .await
            .map_err(|_| unavailable("resume_budget"))?
    }
    fn follow(
        &self,
        scope: TaskScope,
        after: TaskCursor,
    ) -> BoxStream<'static, Result<FollowItem, StoreError>> {
        self.follow_stream(scope, after)
    }
    async fn list(
        &self,
        scope: &TaskScope,
        filter: TaskFilter,
        page: PageRequest,
    ) -> Result<Page<TaskView>, StoreError> {
        tokio::time::timeout(
            self.config.scan_duration,
            self.list_tasks(scope, filter, page),
        )
        .await
        .map_err(|_| unavailable("task_list_budget"))?
    }
}
