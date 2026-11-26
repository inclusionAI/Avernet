use super::*;
#[async_trait]
impl DispatchControlPort for StoreDispatch {
    fn scan_budget_profile(
        &self,
    ) -> Option<bcs_service_api::port::repo::distributed::budget::ScanBudgetProfile> {
        Some(self.stores.scan_budget.profile())
    }
    async fn create(
        &self,
        request: CreateCommittedDispatch,
    ) -> Result<IdempotentWrite, StoreError> {
        validate_spec(&request.spec, &request.source)?;
        let (intent, existing) = self
            .authorize(
                &request.source,
                EffectKind::CreateDispatch { spec: request.spec },
            )
            .await?;
        if let Some(result) = existing {
            self.repair_existing(&intent).await?;
            return Ok(write_result(result, false));
        }
        let (result, new) = self.execute(&intent).await?;
        Ok(write_result(result, new))
    }
    async fn request_cancel(
        &self,
        request: CommittedCancelIntent,
    ) -> Result<ControlOutcome, StoreError> {
        if request.intent.request_id.0.is_empty()
            || request.intent.request_id.0.len() > 256
            || request
                .intent
                .reason
                .as_ref()
                .is_some_and(|r| r.len() > 4096)
        {
            return Err(invalid("dispatch_cancel"));
        }
        let (intent, existing) = self
            .authorize(
                &request.source,
                EffectKind::RequestCancel {
                    did: request.did,
                    intent: request.intent,
                },
            )
            .await?;
        if let Some(result) = existing {
            self.repair_existing(&intent).await?;
            return Ok(control_result(result, false));
        }
        let (result, new) = self.execute(&intent).await?;
        Ok(control_result(result, new))
    }
    async fn push_input(
        &self,
        request: CommittedDispatchInput,
    ) -> Result<ControlOutcome, StoreError> {
        if request.input.seq.0 == 0
            || request.input.input_id.0.is_empty()
            || request.input.input_id.0.len() > 256
            || request.input.business_digest.0.len() > 256
        {
            return Err(invalid("dispatch_input"));
        }
        let (intent, existing) = self
            .authorize(
                &request.source,
                EffectKind::PushInput {
                    did: request.did,
                    input: request.input,
                },
            )
            .await?;
        if let Some(result) = existing {
            self.repair_existing(&intent).await?;
            return Ok(control_result(result, false));
        }
        let (result, new) = self.execute(&intent).await?;
        Ok(control_result(result, new))
    }
    async fn notify(&self, pool: &PoolId, did: &Did) -> Result<(), StoreError> {
        let doc = self
            .document(did)
            .await?
            .ok_or_else(|| unavailable("dispatch_missing"))?;
        if doc.value.view.spec.pool != *pool {
            return Err(invalid("dispatch_pool"));
        }
        let key = self
            .layout
            .command(pool, self.config.command_streams.partition_of(did))?;
        let fields =
            BTreeMap::from([(crate::command::DID_FIELD.into(), did.0.as_bytes().to_vec())]);
        self.io(
            async {
                self.stores
                    .streams
                    .append(&key, AppendId::Auto, &fields)
                    .await
                    .map(|_| ())
                    .map_err(|e| crate::error::from_write(&e, &key))
            },
            true,
            &key,
        )
        .await?;
        // Only disposable hints are trimmed; the journal is the recovery source.
        self.io(
            async {
                self.stores
                    .streams
                    .trim(&key, TrimPolicy::MaxLen(4096))
                    .await
                    .map(|_| ())
                    .map_err(|e| after_write(crate::error::from_write(&e, &key), &key))
            },
            true,
            &key,
        )
        .await
    }
    async fn get(&self, did: &Did) -> Result<Option<DispatchRecord>, StoreError> {
        Ok(self.document(did).await?.map(|mut d| {
            d.value.discard_confirmed();
            DispatchRecord {
            view: d.value.view,
            revision: Revision(d.version.0.to_string()),
            terminal_evidence: d.value.connector.and_then(|c| c.finish_evidence),
        }
        }))
    }
    async fn scan_due(
        &self,
        shard: ShardId,
        now: TimestampMillis,
        page: PageRequest,
    ) -> Result<Page<Did>, StoreError> {
        self.scan(shard, now, page).await
    }
    async fn reschedule(
        &self,
        did: &Did,
        expected: Revision,
        at: TimestampMillis,
    ) -> Result<RecheckOutcome, StoreError> {
        let key = self.layout.disp(did)?;
        let Some(mut doc) = self.document(did).await? else {
            return Err(unavailable("dispatch_missing"));
        };
        if expected.0 != doc.version.0.to_string() {
            return Ok(RecheckOutcome::NeedsRecheck);
        }
        let ended = doc.value.ended();
        doc.value.due = if ended { None } else { Some(at) };
        let mut patch = serde_json::Map::new();
        patch.insert("due".into(), encode_json(&doc.value.due)?);
        let before = doc.value.view.control.pending_input.len();
        doc.value.discard_confirmed();
        if before != doc.value.view.control.pending_input.len() {
            patch.insert(
                "control".into(),
                serde_json::json!({"pending_input": doc.value.view.control.pending_input}),
            );
        }
        if !self
            .mutate_disp(&key, Some(doc.version), &doc.value, patch)
            .await?
        {
            return Ok(RecheckOutcome::NeedsRecheck);
        }
        let _ = self.hint(&doc.value).await;
        Ok(if ended {
            RecheckOutcome::Ended
        } else {
            RecheckOutcome::Applied
        })
    }
}
impl StoreDispatch {
    async fn repair_existing(&self, intent: &Intent) -> Result<(), StoreError> {
        // Normally the directory survives forever. A lost create reply still follows
        // this path and finishes the root slot before reporting success.
        self.clear_pending(intent).await?;
        if let Ok(Some(doc)) = self.document(intent.did()?).await {
            let _ = self.hint(&doc.value).await;
        }
        Ok(())
    }
}
fn write_result(result: ReceiptResult, new: bool) -> IdempotentWrite {
    match result {
        ReceiptResult::Conflict => IdempotentWrite::ContentConflict,
        _ => {
            if new {
                IdempotentWrite::Applied
            } else {
                IdempotentWrite::AlreadyApplied
            }
        }
    }
}
fn control_result(result: ReceiptResult, new: bool) -> ControlOutcome {
    match result {
        ReceiptResult::Conflict => ControlOutcome::ContentConflict,
        ReceiptResult::Superseded { outcome, evidence } => {
            ControlOutcome::SupersededTerminal { outcome, evidence }
        }
        ReceiptResult::Applied => {
            if new {
                ControlOutcome::Applied
            } else {
                ControlOutcome::AlreadyApplied
            }
        }
    }
}
fn validate_spec(spec: &DispatchSpec, source: &EffectSource) -> Result<(), StoreError> {
    if spec.schema_version != DISPATCH_SCHEMA_VERSION {
        return Err(StoreError::UnsupportedFormat {
            format: spec.schema_version,
        });
    }
    if spec.sid != source.commit.sid
        || !spec.tid.0.starts_with(&format!("{}.", spec.sid.0))
        || !spec.did.0.starts_with(&format!("{}.", spec.tid.0))
        || spec.attempt == 0
        || bytes(spec)? > 68 * 1024
    {
        return Err(invalid("dispatch_spec"));
    }
    Ok(())
}
