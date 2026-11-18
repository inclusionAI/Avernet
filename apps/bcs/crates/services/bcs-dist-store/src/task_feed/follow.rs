use super::*;
use futures::{StreamExt, stream::BoxStream};
use storage::bounded_bytes;

// A resolved unit may use the entire 1 MiB output allowance. The extra 64 KiB
// bounds its event/cursor envelope; native stream pages retain their 1 MiB cap.
const DELIVERY_BYTES: usize = 1024 * 1024 + 64 * 1024;
struct FollowState {
    store: StoreTaskFeed,
    scope: TaskScope,
    cursor: TaskCursor,
    task: Option<TaskSnapshot>,
    permit: Option<tokio::sync::OwnedSemaphorePermit>,
    done: bool,
}
impl StoreTaskFeed {
    pub(super) fn follow_stream(
        &self,
        scope: TaskScope,
        cursor: TaskCursor,
    ) -> BoxStream<'static, Result<FollowItem, StoreError>> {
        let state = FollowState {
            store: self.clone(),
            scope,
            cursor,
            task: None,
            permit: None,
            done: false,
        };
        futures::stream::unfold(state, |mut state| async move {
            if state.done {
                return None;
            }
            let result: Result<FollowItem, StoreError> = async {
                if state.permit.is_none() {
                    state.permit = Some(
                        state
                            .store
                            .followers
                            .clone()
                            .try_acquire_owned()
                            .map_err(|_| unavailable("follow_capacity"))?,
                    );
                }
                if state.task.is_none() {
                    state.store.cursor_after(&state.cursor)?;
                    let task = state
                        .store
                        .task(&state.scope, &state.cursor.tid)
                        .await?
                        .ok_or_else(|| invalid("follow_task_missing"))?
                        .value;
                    if task.sid != state.cursor.sid {
                        return Err(invalid("follow_scope"));
                    }
                    state
                        .store
                        .projection
                        .validate_limits(&task.output_limits)
                        .map_err(|_| invalid("follow_limits"))?;
                    bounded_bytes(&task, 256 * 1024)?;
                    state.task = Some(task);
                }
                state
                    .store
                    .follow_next(
                        state.task.as_ref().ok_or_else(|| invalid("follow_task"))?,
                        &mut state.cursor,
                    )
                    .await
            }
            .await;
            state.done = match &result {
                Ok(FollowItem::Gap) | Err(_) => true,
                Ok(FollowItem::Events { items, .. }) => items
                    .iter()
                    .any(|event| matches!(event.body, OutputBody::Terminal { .. })),
            };
            if state.done {
                state.permit = None;
                state.task = None;
            }
            Some((result, state))
        })
        .boxed()
    }

    async fn follow_next(
        &self,
        task: &TaskSnapshot,
        cursor: &mut TaskCursor,
    ) -> Result<FollowItem, StoreError> {
        let position = self.cursor_after(cursor)?;
        loop {
            if self.has_gap(task, position).await? {
                return Ok(FollowItem::Gap);
            }
            let (_, head) = self.head(&cursor.sid).await?;
            if position > head.completed_head {
                return Err(invalid("output_cursor_future"));
            }
            if let Some(through) = head.completed_head
                && position < Some(through)
            {
                let events = self
                    .page(&self.layout.ev(&cursor.sid)?, position, through)
                    .await?;
                let mut scanned = position;
                let mut bytes = 0usize;
                let mut items = Vec::new();
                if events.is_empty() {
                    scanned = Some(through);
                }
                for mut event in events {
                    let id = event.id;
                    if event.tid == cursor.tid {
                        self.resolve_follow_event(task, &mut event).await?;
                        let size = bounded_bytes(&event, DELIVERY_BYTES - 4096)?.len();
                        if size > (DELIVERY_BYTES - 4096).saturating_sub(bytes) {
                            break;
                        }
                        bytes += size;
                        items.push(event);
                    }
                    scanned = Some(id);
                }
                if self.has_gap(task, position).await? {
                    return Ok(FollowItem::Gap);
                }
                let next = self.cursor(&cursor.sid, &cursor.tid, scanned)?;
                *cursor = next.clone();
                return Ok(FollowItem::Events { items, next });
            }
            tokio::time::sleep(self.config.poll_interval).await;
        }
    }
    async fn resolve_follow_event(
        &self,
        task: &TaskSnapshot,
        event: &mut OutputEvent,
    ) -> Result<(), StoreError> {
        match &mut event.body {
            OutputBody::Artifact { update, .. } => {
                let payload = match update {
                    ArtifactUpdate::Upsert { content } => content,
                    ArtifactUpdate::AppendText { delta } => delta,
                };
                let value = self
                    .payload(payload, task.output_limits.max_unit_bytes as usize)
                    .await?;
                *payload = Payload::Inline(value);
            }
            OutputBody::Terminal { status, result_ref } => {
                self.final_record(task, result_ref, *status).await?;
            }
            OutputBody::Status { .. } => {}
        }
        Ok(())
    }
}
