//! Retained terminal evidence remains verifiable after physical output trimming.
use super::*;
use bcs_service_api::port::repo::distributed::admission::{
    TerminalTaskEvidence, TerminalTaskEvidencePort,
};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct TerminalMarker {
    effect: EffectId,
    completion: Completion,
    kind: EffectKind,
}
impl StoreTaskFeed {
    pub(super) async fn retain_terminal(
        &self,
        source: &EffectSource,
        kind: EffectKind,
    ) -> Result<(), StoreError> {
        let tids = match &kind {
            EffectKind::AppendEvents { events, .. } => events
                .iter()
                .filter(|e| matches!(e.body, OutputBody::Terminal { .. }))
                .map(|e| e.tid.clone())
                .collect::<Vec<_>>(),
            EffectKind::PublishSnapshot { snapshot }
                if publish::terminal_status(snapshot.content.status).is_some() =>
            {
                vec![snapshot.tid.clone()]
            }
            _ => return Ok(()),
        };
        if tids.is_empty() {
            return Ok(());
        }
        let completion = self
            .completion(&source.effect_id)
            .await?
            .ok_or_else(|| unavailable("terminal_completion_missing"))?;
        if completion.identity != completion_identity(source)?
            || completion.digest != digest_json(&encode_json(&kind)?)
        {
            return Err(invalid("terminal_completion_conflict"));
        }
        let marker = TerminalMarker {
            effect: source.effect_id.clone(),
            completion,
            kind,
        };
        for tid in tids {
            let key = match marker.kind {
                EffectKind::AppendEvents { .. } => self.layout.task_terminal_append(&tid)?,
                _ => self.layout.task_terminal_publish(&tid)?,
            };
            if let Some(existing) = self.read::<TerminalMarker>(&key).await? {
                if existing.value != marker {
                    return Err(invalid("terminal_marker_conflict"));
                }
                continue;
            }
            match self.write(&key, None, &marker, &key).await {
                Ok(true) => {}
                result => match self.read::<TerminalMarker>(&key).await {
                    Ok(Some(stored)) if stored.value == marker => {}
                    Ok(Some(_)) => return Err(invalid("terminal_marker_conflict")),
                    _ => {
                        return Err(match result {
                            Err(e) => e,
                            _ => unknown(&key),
                        });
                    }
                },
            }
        }
        Ok(())
    }
    async fn verify_marker(&self, marker: &TerminalMarker) -> Result<(), StoreError> {
        let Some(completion) = self.completion(&marker.effect).await? else {
            return Err(unavailable("terminal_completion_missing"));
        };
        if completion != marker.completion
            || completion.schema_version != 1
            || completion.digest != digest_json(&encode_json(&marker.kind)?)
        {
            return Err(invalid("terminal_marker_digest"));
        }
        let identity: serde_json::Value = serde_json::from_str(&completion.identity)
            .map_err(|_| invalid("terminal_marker_identity"))?;
        if identity["effect_id"] != encode_json(&marker.effect)?
            || identity["effect_digest"] != encode_json(&completion.digest)?
            || identity["sequence"] != encode_json(&marker.effect.commit)?
        {
            return Err(invalid("terminal_marker_identity"));
        }
        Ok(())
    }
    async fn terminal_evidence_inner(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<TerminalTaskEvidence>, StoreError> {
        let Some(task) = self.task(scope, tid).await?.map(|t| t.value) else {
            return Ok(None);
        };
        let Some(status) = publish::terminal_status(task.content.status) else {
            return Ok(None);
        };
        self.projection
            .validate_task_limits(&task, &task.output_limits)
            .map_err(|_| invalid("terminal_frozen_limits"))?;
        let Some(append) = self
            .read::<TerminalMarker>(&self.layout.task_terminal_append(tid)?)
            .await?
            .map(|m| m.value)
        else {
            return Ok(None);
        };
        let Some(publication) = self
            .read::<TerminalMarker>(&self.layout.task_terminal_publish(tid)?)
            .await?
            .map(|m| m.value)
        else {
            return Ok(None);
        };
        self.verify_marker(&append).await?;
        self.verify_marker(&publication).await?;
        let EffectKind::PublishSnapshot { snapshot } = &publication.kind else {
            return Err(invalid("terminal_publication_kind"));
        };
        if *snapshot != task
            || publication.effect.sid != task.sid
            || publication.effect.commit != task.source_commit
        {
            return Err(invalid("terminal_publication_snapshot"));
        }
        if publication.completion.operation
            != (CompletedOperation::Publish {
                tid: tid.clone(),
                source_commit: task.source_commit,
            })
        {
            return Err(invalid("terminal_publication_operation"));
        }
        let EffectKind::AppendEvents { previous, events } = &append.kind else {
            return Err(invalid("terminal_append_kind"));
        };
        if append.effect.sid != task.sid || events.is_empty() {
            return Err(invalid("terminal_append_identity"));
        }
        if append.completion.operation
            != (CompletedOperation::Append {
                previous: *previous,
                first: events[0].id,
                last: events[events.len() - 1].id,
            })
        {
            return Err(invalid("terminal_append_operation"));
        }
        let mut matching = events
            .iter()
            .filter(|event| event.tid == *tid && matches!(event.body, OutputBody::Terminal { .. }));
        let Some(event) = matching.next() else {
            return Err(invalid("terminal_event_missing"));
        };
        if matching.next().is_some()
            || event.id.commit != append.effect.commit
            || task
                .snapshot_event_id
                .is_none_or(|covered| covered < event.id)
        {
            return Err(invalid("terminal_event_coverage"));
        }
        let OutputBody::Terminal {
            status: event_status,
            result_ref,
        } = &event.body
        else {
            return Err(invalid("terminal_event_kind"));
        };
        if *event_status != status
            || task.content.result_ref.as_ref() != Some(result_ref)
            || !task.content.interactions.is_empty()
        {
            return Err(invalid("terminal_event_content"));
        }
        let record = self.final_record(&task, result_ref, status).await?;
        let display = self
            .display(&task.content.snapshot, &task.output_limits)
            .await?;
        if self.display(&record.snapshot, &task.output_limits).await? != display {
            return Err(invalid("terminal_archive_display"));
        }
        let (_, head) = self.head(&task.sid).await?;
        if head.completed_head.is_none_or(|id| id < event.id) {
            return Err(invalid("terminal_head_coverage"));
        }
        Ok(Some(TerminalTaskEvidence {
            scope: scope.clone(),
            sid: task.sid.clone(),
            tid: tid.clone(),
            terminal_event: event.id,
            source_commit: task.source_commit,
            result_ref: result_ref.clone(),
            archive_digest: digest_json(&encode_json(&record)?),
            publication_digest: digest_json(
                &serde_json::json!({"append":append,"publication":publication,"task":task}),
            ),
        }))
    }
}
#[async_trait]
impl TerminalTaskEvidencePort for StoreTaskFeed {
    async fn terminal_evidence(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<TerminalTaskEvidence>, StoreError> {
        tokio::time::timeout(
            self.config.scan_duration,
            self.terminal_evidence_inner(scope, tid),
        )
        .await
        .map_err(|_| unavailable("terminal_evidence_timeout"))?
    }
}
