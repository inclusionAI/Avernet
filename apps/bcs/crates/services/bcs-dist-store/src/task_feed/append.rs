use super::*;
use storage::{MAX_EVENT_BYTES, bounded_bytes, display_event};

impl StoreTaskFeed {
    pub(super) async fn append_batch(
        &self,
        request: AppendCommittedEvents,
    ) -> Result<AppendOutcome, StoreError> {
        if request.events.is_empty() || request.events.len() > 32 {
            return Err(invalid("output_batch_count"));
        }
        bounded_bytes(&request.events, 256 * 1024)?;
        let kind = EffectKind::AppendEvents {
            previous: request.previous,
            events: request.events.clone(),
        };
        bounded_bytes(&kind, 256 * 1024)?;
        let digest = digest_json(&encode_json(&kind)?);
        let first = request
            .events
            .first()
            .ok_or_else(|| invalid("empty_output"))?
            .id;
        let last = request
            .events
            .last()
            .ok_or_else(|| invalid("empty_output"))?
            .id;
        let completion = Completion {
            schema_version: 1,
            identity: completion_identity(&request.source)?,
            digest: digest.clone(),
            operation: CompletedOperation::Append {
                previous: request.previous,
                first,
                last,
            },
        };
        if let Some(existing) = self.completion(&request.source.effect_id).await? {
            if existing != completion {
                return Ok(AppendOutcome::ContentConflict);
            }
            self.finish_append(&request.source, &completion).await?;
            return self.append_replay_outcome(&request.source, last).await;
        }
        if request.source.content_digest != digest {
            return Err(invalid("output_effect_digest"));
        }
        if let Err(error) = self.io(self.verifier.verify(&request.source, &kind)).await {
            // Completion can race the initial absence and current-head lookup.
            if let Some(existing) = self.completion(&request.source.effect_id).await? {
                if existing != completion {
                    return Ok(AppendOutcome::ContentConflict);
                }
                self.finish_append(&request.source, &completion).await?;
                return self.append_replay_outcome(&request.source, last).await;
            }
            return Err(error);
        }
        let mut previous = request.previous;
        let mut tasks = BTreeMap::new();
        let mut encoded = Vec::with_capacity(request.events.len());
        for event in &request.events {
            if event.id.commit != request.source.commit.sequence
                || previous.is_some_and(|id| id >= event.id)
                || !event
                    .tid
                    .0
                    .starts_with(&format!("{}.", request.source.commit.sid.0))
            {
                return Err(invalid("output_event_identity"));
            }
            previous = Some(event.id);
            if !tasks.contains_key(&event.tid) {
                let task = self
                    .read::<TaskSnapshot>(&self.layout.task(&event.tid)?)
                    .await?
                    .ok_or_else(|| unavailable("output_task_missing"))?
                    .value;
                if task.sid != request.source.commit.sid || task.tid != event.tid {
                    return Err(invalid("output_task_identity"));
                }
                self.projection
                    .validate_limits(&task.output_limits)
                    .map_err(|_| invalid("output_task_limits"))?;
                tasks.insert(event.tid.clone(), task);
            }
            self.validate_event(
                event,
                tasks
                    .get(&event.tid)
                    .ok_or_else(|| invalid("output_task_missing"))?,
            )
            .await?;
            encoded.push(bounded_bytes(event, MAX_EVENT_BYTES)?);
        }
        let reservation = AppendReservation {
            identity: completion.identity.clone(),
            digest,
            effect: request.source.effect_id.clone(),
            previous: request.previous,
            first,
            last,
        };
        let key = self.layout.output_head(&request.source.commit.sid)?;
        let mut budget = CasBudget::new();
        loop {
            budget.attempt(&key).await?;
            let (version, mut head) = budget
                .io(&key, self.head(&request.source.commit.sid))
                .await?;
            if let Some(pending) = &head.pending {
                if pending != &reservation {
                    return Ok(AppendOutcome::OutOfOrder);
                }
                break;
            }
            if head.completed_head != request.previous {
                return Ok(AppendOutcome::OutOfOrder);
            }
            if version.is_none() {
                // A lost head must not authorize resetting an extant stream.
                let existing = budget
                    .io(&key, async {
                        self.stores
                            .streams
                            .range(
                                &self.layout.ev(&request.source.commit.sid)?,
                                Bound::Unbounded,
                                Bound::Unbounded,
                                NonZeroU32::MIN,
                                true,
                            )
                            .await
                            .map_err(|e| crate::error::from_read(&e))
                    })
                    .await?;
                if !existing.entries.is_empty() {
                    return Err(invalid("output_head_missing"));
                }
            }
            head.pending = Some(reservation.clone());
            if budget
                .io(&key, self.write(&key, version, &head, &key))
                .await?
            {
                break;
            }
        }
        for (event, bytes) in request.events.iter().zip(encoded) {
            self.append_one(
                &self.layout.ev(&request.source.commit.sid)?,
                event.id,
                &request.source.effect_id,
                bytes.clone(),
            )
            .await?;
            // A per-task display stream bounds restore independently of other
            // tasks and of unlimited ephemeral Thinking traffic. Both streams
            // must be confirmed before the batch becomes visible.
            if display_event(event) {
                self.append_one(
                    &self.layout.task_display_ev(&event.tid)?,
                    event.id,
                    &request.source.effect_id,
                    bytes,
                )
                .await?;
            }
        }
        self.complete(&request.source.effect_id, &completion)
            .await?;
        self.finish_append(&request.source, &completion).await?;
        Ok(AppendOutcome::Appended)
    }

    async fn append_one(
        &self,
        key: &str,
        id: EventId,
        effect: &EffectId,
        bytes: Vec<u8>,
    ) -> Result<(), StoreError> {
        let fields = BTreeMap::from([
            ("event".to_owned(), bytes),
            ("effect".to_owned(), bounded_bytes(effect, 1024)?),
        ]);
        let result = tokio::time::timeout(
            self.config.io_timeout,
            self.stores
                .streams
                .append(key, AppendId::Explicit(stream_id(id)), &fields),
        )
        .await;
        match result {
            Ok(Ok(actual)) if actual == stream_id(id) => return Ok(()),
            Ok(Err(
                bcs_store_api::StoreError::IdNotIncreasing
                | bcs_store_api::StoreError::OutcomeUnknown(_),
            ))
            | Err(_) => {}
            Ok(Err(error)) => return Err(after_write(crate::error::from_write(&error, key), key)),
            Ok(Ok(_)) => return Err(invalid("output_append_id")),
        }
        let page = self
            .io(async {
                self.stores
                    .streams
                    .range(
                        key,
                        Bound::Included(stream_id(id)),
                        Bound::Included(stream_id(id)),
                        NonZeroU32::MIN,
                        false,
                    )
                    .await
                    .map_err(|e| crate::error::from_read(&e))
            })
            .await;
        match page {
            Ok(page)
                if page.entries.len() == 1
                    && page.entries[0].id == stream_id(id)
                    && page.entries[0].fields == fields =>
            {
                Ok(())
            }
            Ok(page) if !page.entries.is_empty() => {
                Err(invalid("output_existing_content_conflict"))
            }
            _ => Err(unknown(key)),
        }
    }

    async fn finish_append(
        &self,
        source: &EffectSource,
        completion: &Completion,
    ) -> Result<(), StoreError> {
        let CompletedOperation::Append {
            previous,
            first,
            last,
        } = completion.operation
        else {
            return Err(invalid("output_completion_kind"));
        };
        let key = self.layout.output_head(&source.commit.sid)?;
        let mut budget = CasBudget::new();
        loop {
            budget.attempt(&key).await?;
            let (version, mut head) = budget.io(&key, self.head(&source.commit.sid)).await?;
            if head.completed_head.is_some_and(|id| id >= last) {
                return Ok(());
            }
            let expected = AppendReservation {
                identity: completion.identity.clone(),
                digest: completion.digest.clone(),
                effect: source.effect_id.clone(),
                previous,
                first,
                last,
            };
            if head.pending.as_ref() != Some(&expected) || head.completed_head != previous {
                return Err(invalid("output_completion_head"));
            }
            head.completed_head = Some(last);
            head.pending = None;
            if budget
                .io(&key, self.write(&key, version, &head, &key))
                .await?
            {
                return Ok(());
            }
        }
    }
    async fn append_replay_outcome(
        &self,
        source: &EffectSource,
        last: EventId,
    ) -> Result<AppendOutcome, StoreError> {
        let (_, head) = self
            .head(&source.commit.sid)
            .await
            .map_err(|error| after_write(error, &source.commit.commit_id.0))?;
        Ok(
            if head.removed_through.is_some_and(|removed| removed >= last) {
                AppendOutcome::Compacted(CompletionCertificate {
                    sid: source.commit.sid.clone(),
                    covered_commit: source.commit.sequence,
                    proof: self.layout.effect_completion(&source.effect_id)?,
                })
            } else {
                AppendOutcome::AlreadyApplied
            },
        )
    }
}
