use super::*;
use storage::bounded_bytes;

impl StoreTaskFeed {
    pub(super) async fn validate_event(
        &self,
        event: &OutputEvent,
        task: &TaskSnapshot,
    ) -> Result<(), StoreError> {
        match &event.body {
            OutputBody::Artifact {
                source,
                artifact_id,
                update,
                ..
            } => {
                if source.0.is_empty()
                    || artifact_id.0.is_empty()
                    || source.0.len() > 256
                    || artifact_id.0.len() > 256
                {
                    return Err(invalid("artifact_identity"));
                }
                let (payload, text) = match update {
                    ArtifactUpdate::Upsert { content } | ArtifactUpdate::UpsertParts { content } => (content, false),
                    ArtifactUpdate::AppendText { delta } => (delta, true),
                    ArtifactUpdate::AppendParts { delta } => (delta, false),
                };
                let value = self
                    .payload(payload)
                    .await?;
                if text && !value.is_string() {
                    return Err(invalid("append_text_type"));
                }
                if matches!(update, ArtifactUpdate::UpsertParts { .. } | ArtifactUpdate::AppendParts { .. })
                    && !value.as_array().is_some_and(|parts| parts.iter().all(serde_json::Value::is_object)) {
                    return Err(invalid("parts_require_objects"));
                }
                if let Payload::Inline(_) = payload {
                    bounded_bytes(&value, task.output_limits.inline_threshold_bytes as usize)?;
                }
            }
            OutputBody::Status {
                status,
                interactions,
            } => {
                if terminal_status(*status).is_some() {
                    return Err(invalid("terminal_requires_archive"));
                }
                if interactions.len() > 64 || bounded_bytes(interactions, MAX_INTERACTION_METADATA_BYTES).is_err() {
                    return Err(invalid("interaction_bound"));
                }
            }
            OutputBody::Terminal { status, result_ref } => {
                self.final_record(task, result_ref, *status).await?;
            }
        }
        Ok(())
    }

    pub(super) async fn final_record(
        &self,
        task: &TaskSnapshot,
        reference: &BlobRef,
        status: TerminalStatus,
    ) -> Result<FinalTaskRecord, StoreError> {
        let record = self
            .io(self.stores.results.get_final(&task.scope, &task.tid))
            .await?
            .ok_or_else(|| unavailable("terminal_archive_missing"))?;
        let bytes = self
            .io(self.stores.blobs.get(reference))
            .await?
            .ok_or_else(|| unavailable("terminal_archive_blob_missing"))?;
        if crate::archive::blob_reference(&reference.key, &bytes).digest != reference.digest {
            return Err(invalid("terminal_archive_digest"));
        }
        let referenced: FinalTaskRecord =
            serde_json::from_slice(&bytes).map_err(|_| invalid("terminal_archive_format"))?;
        if record != referenced
            || record.scope != task.scope
            || record.sid != task.sid
            || record.tid != task.tid
            || record.status != status
        {
            return Err(invalid("terminal_archive_identity"));
        }
        Ok(record)
    }

    pub(super) async fn publish_snapshot(
        &self,
        request: PublishCommittedSnapshot,
    ) -> Result<PublishOutcome, StoreError> {
        let snapshot = &request.snapshot;
        let source = &request.source;
        if snapshot.sid != source.commit.sid || snapshot.source_commit != source.commit.sequence {
            return Err(invalid("snapshot_source"));
        }
        bounded_bytes(snapshot, 256 * 1024)?;
        if snapshot.content.interactions.len() > 64 {
            return Err(invalid("snapshot_interactions"));
        }
        bounded_bytes(&snapshot.content.interactions, 64 * 1024)?;
        let kind = EffectKind::PublishSnapshot {
            snapshot: snapshot.clone(),
        };
        bounded_bytes(&kind, 256 * 1024)?;
        let completion = Completion {
            schema_version: 1,
            identity: completion_identity(source)?,
            digest: digest_json(&encode_json(&kind)?),
            operation: CompletedOperation::Publish {
                tid: snapshot.tid.clone(),
                source_commit: snapshot.source_commit,
            },
        };
        if let Some(existing) = self.completion(&source.effect_id).await? {
            return Ok(if existing == completion {
                PublishOutcome::AlreadyApplied
            } else {
                PublishOutcome::ContentConflict
            });
        }
        if source.content_digest != completion.digest {
            return Err(invalid("snapshot_effect_digest"));
        }
        if let Err(error) = self.io(self.verifier.verify(source, &kind)).await {
            if let Some(existing) = self.completion(&source.effect_id).await? {
                return Ok(if existing == completion {
                    PublishOutcome::AlreadyApplied
                } else {
                    PublishOutcome::ContentConflict
                });
            }
            return Err(error);
        }
        let key = self.layout.task(&snapshot.tid)?;
        let resolved = self
            .display(&snapshot.content.snapshot, &snapshot.output_limits)
            .await?;
        if let Some(status) = terminal_status(snapshot.content.status) {
            let reference = snapshot
                .content
                .result_ref
                .as_ref()
                .ok_or_else(|| invalid("terminal_result_ref"))?;
            let record = self.final_record(snapshot, reference, status).await?;
            let archived = self
                .display(&record.snapshot, &snapshot.output_limits)
                .await?;
            if archived != resolved || !snapshot.content.interactions.is_empty() {
                return Err(invalid("terminal_snapshot_content"));
            }
        } else if snapshot.content.result_ref.is_some() {
            return Err(invalid("nonterminal_result_ref"));
        }
        let (_, head) = self.head(&snapshot.sid).await?;
        if snapshot.snapshot_event_id > head.completed_head {
            return Err(unavailable("snapshot_append_incomplete"));
        }
        let mut budget = CasBudget::new();
        loop {
            budget.attempt(&key).await?;
            let current = budget
                .io(&key, self.task(&snapshot.scope, &snapshot.tid))
                .await?
                .ok_or_else(|| unavailable("snapshot_task_missing"))?;
            self.projection
                .validate_task_limits(snapshot, &current.value.output_limits)
                .map_err(|_| invalid("snapshot_frozen_limits"))?;
            if snapshot.sid != current.value.sid || snapshot.created_at != current.value.created_at
            {
                return Err(invalid("snapshot_frozen_identity"));
            }
            if current.value.source_commit > snapshot.source_commit {
                // Only a verified original effect may be classified superseded.
                self.complete(&source.effect_id, &completion).await?;
                return Ok(PublishOutcome::Superseded {
                    source_commit: current.value.source_commit,
                });
            }
            if current.value.source_commit == snapshot.source_commit {
                if current.value != *snapshot {
                    return Ok(PublishOutcome::ContentConflict);
                }
                self.complete(&source.effect_id, &completion).await?;
                return Ok(PublishOutcome::AlreadyApplied);
            }
            if snapshot.snapshot_event_id < current.value.snapshot_event_id
                || snapshot.output_usage.cumulative_bytes
                    < current.value.output_usage.cumulative_bytes
                || terminal_status(current.value.content.status).is_some()
            {
                return Err(invalid("snapshot_regression"));
            }
            if budget
                .io(
                    &key,
                    self.write(&key, Some(current.version), snapshot, &key),
                )
                .await?
            {
                self.complete(&source.effect_id, &completion).await?;
                return Ok(PublishOutcome::Published);
            }
        }
    }
}

pub(super) fn terminal_status(status: TaskStatus) -> Option<TerminalStatus> {
    match status {
        TaskStatus::Completed => Some(TerminalStatus::Completed),
        TaskStatus::Failed => Some(TerminalStatus::Failed),
        TaskStatus::Canceled => Some(TerminalStatus::Canceled),
        TaskStatus::Rejected => Some(TerminalStatus::Rejected),
        _ => None,
    }
}
pub(super) fn task_status(status: TerminalStatus) -> TaskStatus {
    match status {
        TerminalStatus::Completed => TaskStatus::Completed,
        TerminalStatus::Failed => TaskStatus::Failed,
        TerminalStatus::Canceled => TaskStatus::Canceled,
        TerminalStatus::Rejected => TaskStatus::Rejected,
    }
}
