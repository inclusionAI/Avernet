use super::*;
use publish::terminal_status;

impl StoreTaskFeed {
    pub(super) async fn trim_output(
        &self,
        request: RetentionRequest,
    ) -> Result<TrimOutcome, StoreError> {
        tokio::time::timeout(self.config.scan_duration, self.trim_round(&request))
            .await
            .map_err(|_| unknown("output_trim_budget"))?
            .map_err(|error| after_write(error, "output_trim"))
    }
    async fn trim_round(&self, request: &RetentionRequest) -> Result<TrimOutcome, StoreError> {
        let (_, head) = self.head(&request.sid).await?;
        if let Some(boundary) = head.keep_from
            && boundary >= request.keep_from
        {
            self.trim_stream(&self.layout.ev(&request.sid)?, boundary)
                .await?;
            return Ok(TrimOutcome::AlreadyTrimmed);
        }
        let Some(fixed_head) = head.completed_head else {
            return Ok(TrimOutcome::NotCovered);
        };
        if request.keep_from > fixed_head {
            return Ok(TrimOutcome::NotCovered);
        }
        let key = self.layout.output_trim(&request.sid)?;
        let mut budget = CasBudget::new();
        let mut progress;
        loop {
            budget.attempt(&key).await?;
            let existing = budget.io(&key, self.read::<TrimProgress>(&key)).await?;
            if let Some(existing) = &existing
                && !existing.value.complete
            {
                progress = existing.clone();
                break;
            }
            let candidate = TrimProgress {
                schema_version: 1,
                keep_from: request.keep_from,
                fixed_head,
                after: head.removed_through,
                verified_through: head.removed_through,
                complete: false,
            };
            if budget
                .io(
                    &key,
                    self.write(
                        &key,
                        existing.map(|stored| stored.version),
                        &candidate,
                        &key,
                    ),
                )
                .await?
            {
                progress = self
                    .read::<TrimProgress>(&key)
                    .await?
                    .ok_or_else(|| unknown(&key))?;
                break;
            }
        }
        // Work resumes the original fixed cut even if a newer caller asks for a
        // larger boundary. It cannot claim that newer request has been trimmed.
        let target = progress.value.keep_from;
        for _ in 0..self.config.scan_pages {
            let page = self
                .stored_page(
                    &self.layout.ev(&request.sid)?,
                    progress.value.after,
                    progress.value.fixed_head,
                )
                .await?;
            let finished = page.is_empty() || page.iter().any(|stored| stored.event.id >= target);
            let mut tasks = BTreeMap::<Tid, (EventId, TaskSnapshot)>::new();
            let mut completions = BTreeMap::<String, Completion>::new();
            let mut through = progress.value.after;
            for stored in page
                .into_iter()
                .take_while(|stored| stored.event.id < target)
            {
                let proof_key = self.layout.effect_completion(&stored.effect)?;
                if !completions.contains_key(&proof_key) {
                    let Some(proof) = self.completion(&stored.effect).await? else {
                        return Ok(TrimOutcome::NotCovered);
                    };
                    completions.insert(proof_key.clone(), proof);
                }
                let Some(Completion {
                    operation: CompletedOperation::Append { first, last, .. },
                    ..
                }) = completions.get(&proof_key)
                else {
                    return Err(invalid("trim_append_proof"));
                };
                if stored.effect.sid != request.sid
                    || stored.event.id < *first
                    || stored.event.id > *last
                {
                    return Err(invalid("trim_append_proof"));
                }
                let event = stored.event;
                if let Some((last, _)) = tasks.get_mut(&event.tid) {
                    *last = event.id;
                } else {
                    let Some(task) = self
                        .read::<TaskSnapshot>(&self.layout.task(&event.tid)?)
                        .await?
                    else {
                        return Ok(TrimOutcome::NotCovered);
                    };
                    if task.value.sid != request.sid || task.value.tid != event.tid {
                        return Err(invalid("trim_task_identity"));
                    }
                    tasks.insert(event.tid.clone(), (event.id, task.value));
                }
                through = Some(event.id);
            }
            for (tid, (last, task)) in &tasks {
                if task.snapshot_event_id.is_none_or(|covered| covered < *last) {
                    return Ok(TrimOutcome::NotCovered);
                }
                if let Some(status) = terminal_status(task.content.status) {
                    let Some(reference) = &task.content.result_ref else {
                        return Ok(TrimOutcome::NotCovered);
                    };
                    self.final_record(task, reference, status).await?;
                }
                self.advance_task_retention(task, *last).await?;
                // Each display stream has its own already-persisted boundary;
                // failure leaves extra bytes and a safe logical Gap. Main-stream
                // deletion waits for the entire fixed cut below.
                self.trim_display_through(&self.layout.task_display_ev(tid)?, *last)
                    .await?;
            }
            let mut candidate = progress.value.clone();
            candidate.after = through;
            candidate.verified_through = through;
            if finished {
                self.advance_session_retention(&request.sid, target, through)
                    .await?;
                self.trim_stream(&self.layout.ev(&request.sid)?, target)
                    .await?;
                candidate.complete = true;
            }
            if !self
                .write(&key, Some(progress.version), &candidate, &key)
                .await?
            {
                return Err(unknown(&key));
            }
            if finished {
                return Ok(if target == request.keep_from {
                    TrimOutcome::Trimmed
                } else {
                    TrimOutcome::NotCovered
                });
            }
            progress = self
                .read::<TrimProgress>(&key)
                .await?
                .ok_or_else(|| unknown(&key))?;
        }
        Err(unknown("output_trim_continue"))
    }

    async fn advance_task_retention(
        &self,
        task: &TaskSnapshot,
        through: EventId,
    ) -> Result<(), StoreError> {
        let key = self.layout.task_retention(&task.tid)?;
        let mut budget = CasBudget::new();
        loop {
            budget.attempt(&key).await?;
            let stored = budget.io(&key, self.read::<TaskRetention>(&key)).await?;
            if let Some(stored) = &stored {
                if stored.value.scope != task.scope || stored.value.sid != task.sid {
                    return Err(invalid("trim_task_scope"));
                }
                if stored.value.removed_through >= through {
                    return Ok(());
                }
            }
            let candidate = TaskRetention {
                schema_version: 1,
                scope: task.scope.clone(),
                sid: task.sid.clone(),
                removed_through: through,
            };
            if budget
                .io(
                    &key,
                    self.write(&key, stored.map(|stored| stored.version), &candidate, &key),
                )
                .await?
            {
                return Ok(());
            }
        }
    }
    async fn advance_session_retention(
        &self,
        sid: &Sid,
        boundary: EventId,
        through: Option<EventId>,
    ) -> Result<(), StoreError> {
        let key = self.layout.output_head(sid)?;
        let mut budget = CasBudget::new();
        loop {
            budget.attempt(&key).await?;
            let (version, mut head) = budget.io(&key, self.head(sid)).await?;
            if head.keep_from.is_some_and(|value| value >= boundary) {
                return Ok(());
            }
            if head.completed_head.is_none_or(|value| value < boundary) {
                return Err(invalid("trim_uncompleted_head"));
            }
            head.keep_from = Some(boundary);
            head.removed_through = head.removed_through.max(through);
            if budget
                .io(&key, self.write(&key, version, &head, &key))
                .await?
            {
                return Ok(());
            }
        }
    }
    async fn trim_display_through(&self, key: &str, through: EventId) -> Result<(), StoreError> {
        // Stream's minor component is u64: index+1 is representable even at
        // EventId's u32 maximum, and removes only this verified prefix.
        let boundary = StreamId {
            major: through.commit.0,
            minor: u64::from(through.index.get()) + 1,
        };
        tokio::time::timeout(
            self.config.io_timeout,
            self.stores.streams.trim(key, TrimPolicy::Before(boundary)),
        )
        .await
        .map_err(|_| unknown(key))?
        .map_err(|error| crate::error::from_write(&error, key))?;
        Ok(())
    }
    async fn trim_stream(&self, key: &str, boundary: EventId) -> Result<(), StoreError> {
        tokio::time::timeout(
            self.config.io_timeout,
            self.stores
                .streams
                .trim(key, TrimPolicy::Before(stream_id(boundary))),
        )
        .await
        .map_err(|_| unknown(key))?
        .map_err(|error| crate::error::from_write(&error, key))?;
        Ok(())
    }
}
