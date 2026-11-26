//! Worker-side read facade: resolve neutral content and apply the output port.
//! Storage remains protocol independent; the Gateway receives formatted JSON.

use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::dist_output::*;
use bcs_service_api::port::repo::distributed::common::{Page, PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::result::BlobStorePort;
use bcs_service_api::port::repo::distributed::task_feed::*;
use futures::StreamExt;
use futures::stream::BoxStream;
use serde_json::Value;
use std::sync::Arc;

pub struct WorkerOutputReader {
    reader: Arc<dyn TaskReaderPort>,
    blobs: Arc<dyn BlobStorePort>,
    output: Arc<dyn DistOutputAdapterPort>,
}

impl WorkerOutputReader {
    pub fn new(
        reader: Arc<dyn TaskReaderPort>,
        blobs: Arc<dyn BlobStorePort>,
        output: Arc<dyn DistOutputAdapterPort>,
    ) -> Self {
        Self { reader, blobs, output }
    }
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.to_owned() }
}

fn format_error(error: OutputAdapterError) -> StoreError {
    invalid(error.code)
}

async fn inline(blobs: &dyn BlobStorePort, payload: Payload) -> Result<Value, StoreError> {
    match payload {
        Payload::Inline(value) => Ok(value),
        Payload::Ref(reference) => {
            let bytes = blobs.get(&reference).await?.ok_or_else(|| invalid("content_blob_missing"))?;
            if bcs_dist_encoding::blob_reference(&reference.key, &bytes).digest != reference.digest {
                return Err(invalid("content_blob_digest"));
            }
            serde_json::from_slice(&bytes).map_err(|_| invalid("content_blob_not_json"))
        }
    }
}

async fn format_view(
    blobs: &dyn BlobStorePort,
    output: &dyn DistOutputAdapterPort,
    view: &mut TaskView,
) -> Result<(), StoreError> {
    let payload = std::mem::replace(&mut view.content.snapshot, Payload::Inline(Value::Null));
    let value = inline(blobs, payload).await?;
    view.content.snapshot = Payload::Inline(output.snapshot(&value).map_err(format_error)?);
    Ok(())
}

async fn format_event(
    blobs: &dyn BlobStorePort,
    output: &dyn DistOutputAdapterPort,
    event: OutputEvent,
) -> Result<FormattedOutputEvent, StoreError> {
    let body = match event.body {
        OutputBody::Status { status, interactions } => FormattedOutputBody::Status { status, interactions },
        OutputBody::Terminal { status, result_ref } => FormattedOutputBody::Terminal { status, result_ref },
        OutputBody::Artifact { source, artifact_id, class, update } => {
            let update = match update {
                ArtifactUpdate::Upsert { content } => ArtifactUpdate::Upsert { content: Payload::Inline(inline(blobs, content).await?) },
                ArtifactUpdate::AppendText { delta } => ArtifactUpdate::AppendText { delta: Payload::Inline(inline(blobs, delta).await?) },
            };
            let content = output.artifact(&ArtifactChange { source, artifact_id, class, update }).map_err(format_error)?;
            FormattedOutputBody::Artifact { content }
        }
    };
    Ok(FormattedOutputEvent { id: event.id, tid: event.tid, body })
}

#[async_trait]
impl DistTaskOutputReaderPort for WorkerOutputReader {
    async fn restore(&self, scope: &TaskScope, tid: &Tid) -> Result<Option<RestoredTask>, StoreError> {
        let Some(mut restored) = self.reader.restore(scope, tid).await? else { return Ok(None); };
        let view = match &mut restored {
            RestoredTask::Final(view) | RestoredTask::Live { view, .. } => view,
        };
        format_view(self.blobs.as_ref(), self.output.as_ref(), view).await?;
        Ok(Some(restored))
    }

    async fn validate_resume(&self, scope: &TaskScope, cursor: &TaskCursor) -> Result<ResumeCheck, StoreError> {
        self.reader.validate_resume(scope, cursor).await
    }

    fn follow(&self, scope: TaskScope, after: TaskCursor) -> BoxStream<'static, Result<FormattedFollowItem, StoreError>> {
        let blobs = self.blobs.clone();
        let output = self.output.clone();
        self.reader.follow(scope, after).then(move |item| {
            let blobs = blobs.clone();
            let output = output.clone();
            async move {
                match item? {
                    FollowItem::Gap => Ok(FormattedFollowItem::Gap),
                    FollowItem::Events { items, next } => {
                        let mut events = Vec::with_capacity(items.len());
                        for event in items {
                            events.push(format_event(blobs.as_ref(), output.as_ref(), event).await?);
                        }
                        Ok(FormattedFollowItem::Events { events, next })
                    }
                }
            }
        }).boxed()
    }

    async fn list(&self, scope: &TaskScope, filter: TaskFilter, page: PageRequest) -> Result<Page<TaskView>, StoreError> {
        let mut page = self.reader.list(scope, filter, page).await?;
        for view in &mut page.items {
            if let Payload::Inline(value) = &view.content.snapshot {
                view.content.snapshot = Payload::Inline(self.output.snapshot(value).map_err(format_error)?);
            }
        }
        Ok(page)
    }
}
