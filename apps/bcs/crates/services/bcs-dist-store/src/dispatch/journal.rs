use super::*;
impl StoreDispatch {
    pub(super) async fn authorize(
        &self,
        source: &EffectSource,
        kind: EffectKind,
    ) -> Result<(Intent, Option<ReceiptResult>), StoreError> {
        let intent = Intent {
            identity: completion_identity(source)?,
            effect: source.effect_id.clone(),
            kind,
        };
        let did = intent.did()?;
        if !did.0.starts_with(&format!("{}.", source.commit.sid.0)) {
            return Err(invalid("dispatch_source_session"));
        }
        if KeyLayout::shard_of(&Sid(did.0.clone()))? != KeyLayout::shard_of(&source.commit.sid)? {
            return Err(invalid("dispatch_source_shard"));
        }
        if bytes(&intent)? > 80 * 1024 {
            return Err(invalid("dispatch_effect_size"));
        }
        if let Some(receipt) = self
            .document(did)
            .await?
            .and_then(|d| d.value.receipt(&intent))
        {
            return Ok((intent, Some(receipt)));
        }
        // A concurrent helper can finish and allow the outbox to advance after the
        // first receipt read. Recheck durable evidence before rejecting that replay.
        if let Err(error) = self
            .io(
                self.verifier.verify(source, &intent.kind),
                false,
                "dispatch_effect_verify",
            )
            .await
        {
            if let Some(receipt) = self
                .document(did)
                .await?
                .and_then(|d| d.value.receipt(&intent))
            {
                return Ok((intent, Some(receipt)));
            }
            return Err(error);
        }
        Ok((intent, None))
    }
    pub(super) async fn reserve(&self, intent: &Intent) -> Result<(), StoreError> {
        let did = intent.did()?;
        let shard = KeyLayout::shard_of(&Sid(did.0.clone()))?;
        let key = self.layout.dispatch_journal(shard);
        let mut budget = Budget::new();
        loop {
            budget.next(&key).await?;
            let (version, mut root) = budget.io(&key, self.journal(shard)).await?;
            if let Some(existing) = root.pending.iter().find(|p| p.effect == intent.effect) {
                return if existing == intent {
                    Ok(())
                } else {
                    Err(invalid("dispatch_intent_conflict"))
                };
            }
            // Recheck after reading the reservation root. A completed retry need not
            // consume quota even if the original root slot was already released.
            if budget
                .io(&key, self.document(did))
                .await?
                .is_some_and(|d| d.value.receipt(intent).is_some())
            {
                return Ok(());
            }
            let size = bytes(intent)?;
            // Reserve receipt plus queued content and the largest subsequent
            // send/remote evidence before making the intent discoverable.
            let retained = size * 2 + 16 * 1024;
            let critical = intent.critical();
            let pending_limit = self.config.max_pending - if critical { 0 } else { 8 };
            let byte_limit = self.config.pending_bytes - if critical { 0 } else { 64 * 1024 };
            if root.pending.len() >= pending_limit || bytes(&root.pending)? + size > byte_limit {
                return Err(unavailable("dispatch_pending_capacity"));
            }
            let entry = if let Some(index) = root.directory.iter().position(|d| d.did == *did) {
                index
            } else {
                if budget
                    .io(&key, self.document(did))
                    .await?
                    .is_some()
                {
                    // A did whose directory row was reclaimed by a shard
                    // batch: the retained dispatch document stays the
                    // authority. Late writes apply against its receipts and
                    // terminal evidence; no discovery row is re-created, so
                    // the reclaimed capacity is never re-consumed.
                    return Ok(());
                }
                if !matches!(intent.kind, EffectKind::CreateDispatch { .. }) {
                    return Err(unavailable("dispatch_missing"));
                }
                if root.directory.len() >= self.config.max_dispatches {
                    return Err(unavailable("dispatch_history_capacity"));
                }
                let seq = std::cmp::max(root.next_seq, 1);
                root.next_seq = seq
                    .checked_add(1)
                    .ok_or_else(|| invalid("dispatch_directory_exhausted"))?;
                root.directory.push(Directory {
                    did: did.clone(),
                    receipts: 0,
                    bytes: 0,
                    seq,
                });
                root.directory.len() - 1
            };
            let count_limit = self.config.max_receipts - if critical { 0 } else { 16 };
            let receipt_limit = self.config.receipt_bytes - if critical { 0 } else { 64 * 1024 };
            let d = &mut root.directory[entry];
            if d.receipts >= count_limit || d.bytes + retained > receipt_limit {
                return Err(unavailable("dispatch_receipt_capacity"));
            }
            d.receipts += 1;
            d.bytes += retained;
            root.pending.push(intent.clone());
            if bytes(&root.directory)? > 256 * 1024 {
                return Err(unavailable("dispatch_directory_capacity"));
            }
            if budget.io(&key, self.write(&key, version, &root)).await? {
                return Ok(());
            }
        }
    }
    pub(super) async fn clear_pending(&self, intent: &Intent) -> Result<(), StoreError> {
        let shard = KeyLayout::shard_of(&Sid(intent.did()?.0.clone()))?;
        let key = self.layout.dispatch_journal(shard);
        let mut budget = Budget::new();
        loop {
            budget.next(&key).await?;
            let (version, mut root) = budget.io(&key, self.journal(shard)).await?;
            let before = root.pending.len();
            root.pending.retain(|p| p != intent);
            if root.pending.len() == before {
                return Ok(());
            }
            if budget.io(&key, self.write(&key, version, &root)).await? {
                return Ok(());
            }
        }
    }
    pub(super) async fn apply(&self, intent: &Intent) -> Result<(ReceiptResult, bool), StoreError> {
        let did = intent.did()?;
        let key = self.layout.disp(did)?;
        let mut budget = Budget::new();
        loop {
            budget.next(&key).await?;
            let current = budget.io(&key, self.document(did)).await?;
            if let Some(d) = &current
                && let Some(receipt) = d.value.receipt(intent)
            {
                return Ok((receipt, false));
            }
            let (version, mut doc, result) = match current {
                None => {
                    let EffectKind::CreateDispatch { spec } = &intent.kind else {
                        return Err(unknown(&key));
                    };
                    (
                        None,
                        Document {
                            view: DispatchView {
                                spec: spec.clone(),
                                control: DispatchControlState {
                                    cancel: None,
                                    pending_input: vec![],
                                },
                                execution: DispatchExecution {
                                    holder: None,
                                    phase: DispatchPhase::Created,
                                    remote: None,
                                    confirmed_input_through: None,
                                    cancellation: CancelObservation::NotSent,
                                    outcome: None,
                                    send_proof: None,
                                    verification: None,
                                    quarantined: None,
                                },
                            },
                            receipts: vec![],
                            due: Some(TimestampMillis(0)),
                            connector: None,
                        },
                        ReceiptResult::Applied,
                    )
                }
                Some(stored) => {
                    let mut doc = stored.value;
                    let result = apply_control(&mut doc, intent)?;
                    (Some(stored.version), doc, result)
                }
            };
            doc.receipts.push(Receipt {
                intent: intent.clone(),
                result: result.clone(),
            });
            doc.due = Some(TimestampMillis(0));
            let mut patch = serde_json::Map::new();
            if version.is_some() {
                doc.discard_confirmed();
                patch.insert("control".into(), encode_json(&doc.view.control)?);
                patch.insert("receipts".into(), encode_json(&doc.receipts)?);
                patch.insert("due".into(), encode_json(&doc.due)?);
            }
            if budget
                .io(&key, self.mutate_disp(&key, version, &doc, patch))
                .await?
            {
                return Ok((result, true));
            }
        }
    }
    pub(super) async fn execute(
        &self,
        intent: &Intent,
    ) -> Result<(ReceiptResult, bool), StoreError> {
        self.reserve(intent).await?;
        let id = self.layout.disp(intent.did()?)?;
        let result = self.apply(intent).await.map_err(|e| after_write(e, &id))?;
        // Durable completion is already authoritative. Hint/notification outages do
        // not invalidate it; the retained directory independently repairs both.
        self.clear_pending(intent)
            .await
            .map_err(|e| after_write(e, &id))?;
        if let Some(doc) = self
            .document(intent.did()?)
            .await
            .map_err(|e| after_write(e, &id))?
        {
            let _ = self.hint(&doc.value).await;
        }
        Ok(result)
    }
}
fn apply_control(doc: &mut Document, intent: &Intent) -> Result<ReceiptResult, StoreError> {
    match &intent.kind {
        EffectKind::CreateDispatch { .. } => Ok(ReceiptResult::Conflict),
        EffectKind::RequestCancel { intent: cancel, .. } => {
            // Distinct committed effects can share the same remote obligation.
            // Keep each accepted request ID bound to its content even when it
            // did not become the remote cancellation identity.
            let changed_request = |old: &CancelIntent| {
                old.request_id == cancel.request_id && old != cancel
            };
            if doc.view.control.cancel.as_ref().is_some_and(changed_request)
                || doc.receipts.iter().any(|receipt| {
                    receipt.result != ReceiptResult::Conflict
                        && matches!(&receipt.intent.kind,
                            EffectKind::RequestCancel { intent, .. } if changed_request(intent))
                })
            {
                return Ok(ReceiptResult::Conflict);
            }
            if let Some(result) = superseded(doc) {
                return Ok(result);
            }
            if doc.view.control.cancel.is_none() {
                doc.view.control.cancel = Some(cancel.clone());
            }
            Ok(ReceiptResult::Applied)
        }
        EffectKind::PushInput { input, .. } => {
            if let Some(old) = doc
                .receipts
                .iter()
                .filter(|r| r.result == ReceiptResult::Applied)
                .find_map(|r| match &r.intent.kind {
                    EffectKind::PushInput { input: old, .. }
                        if old.seq == input.seq || old.input_id == input.input_id =>
                    {
                        Some(old)
                    }
                    _ => None,
                })
            {
                return Ok(if old == input {
                    ReceiptResult::Applied
                } else {
                    ReceiptResult::Conflict
                });
            }
            if let Some(result) = superseded(doc) {
                return Ok(result);
            }
            if input.seq.0 == 0 {
                return Err(invalid("dispatch_input_zero"));
            }
            doc.view.control.pending_input.push(input.clone());
            doc.view.control.pending_input.sort_by_key(|p| p.seq.0);
            Ok(ReceiptResult::Applied)
        }
        _ => Err(invalid("dispatch_effect_kind")),
    }
}
fn superseded(doc: &Document) -> Option<ReceiptResult> {
    doc.final_outcome()
        .zip(doc.connector.as_ref().and_then(|c| c.finish_evidence.clone()))
        .map(|(outcome, evidence)| ReceiptResult::Superseded { outcome, evidence })
}
