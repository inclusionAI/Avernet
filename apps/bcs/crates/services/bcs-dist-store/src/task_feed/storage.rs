use super::*;
use std::{future::Future, io::Write};

pub(super) const MAX_EVENT_BYTES: usize = 128 * 1024;
pub(super) const MAX_ENTRY_BYTES: usize = MAX_EVENT_BYTES + 1024;
pub(super) struct StoredOutput {
    pub event: OutputEvent,
    pub effect: EffectId,
}
impl StoreTaskFeed {
    pub(super) async fn io<T>(
        &self,
        future: impl Future<Output = Result<T, StoreError>>,
    ) -> Result<T, StoreError> {
        tokio::time::timeout(self.config.io_timeout, future)
            .await
            .map_err(|_| unavailable("task_feed_read_timeout"))?
    }
    pub(super) async fn read<T: DeserializeOwned>(
        &self,
        key: &str,
    ) -> Result<Option<Versioned<T>>, StoreError> {
        self.io(async {
            self.stores
                .documents
                .read_document(key)
                .await
                .map_err(|e| crate::error::from_read(&e))?
                .map(|stored| {
                    Ok(Versioned {
                        version: stored.version,
                        value: decode_json(stored.value)?,
                    })
                })
                .transpose()
        })
        .await
    }
    pub(super) async fn write<T: Serialize>(
        &self,
        key: &str,
        expected: Option<Version>,
        value: &T,
        operation: &str,
    ) -> Result<bool, StoreError> {
        bounded_bytes(value, 1024 * 1024)?;
        let value = encode_json(value)?;
        let outcome = tokio::time::timeout(self.config.io_timeout, async {
            match expected {
                Some(version) => {
                    self.stores
                        .documents
                        .replace_document(key, version, &value)
                        .await
                }
                None => self.stores.documents.create_document(key, &value).await,
            }
        })
        .await
        .map_err(|_| unknown(operation))?
        .map_err(|e| crate::error::from_write(&e, operation))?;
        Ok(matches!(outcome, WriteOutcome::Applied { .. }))
    }
    pub(super) async fn head(
        &self,
        sid: &Sid,
    ) -> Result<(Option<Version>, OutputHead), StoreError> {
        Ok(
            match self
                .read::<OutputHead>(&self.layout.output_head(sid)?)
                .await?
            {
                Some(stored) => (Some(stored.version), stored.value),
                None => (None, OutputHead::default()),
            },
        )
    }
    pub(super) async fn task(
        &self,
        scope: &TaskScope,
        tid: &Tid,
    ) -> Result<Option<Versioned<TaskSnapshot>>, StoreError> {
        let task = self.read::<TaskSnapshot>(&self.layout.task(tid)?).await?;
        match task {
            Some(task) if task.value.scope == *scope && task.value.tid == *tid => Ok(Some(task)),
            Some(_) => Err(invalid("task_scope")),
            None => Ok(None),
        }
    }
    pub(super) async fn completion(
        &self,
        effect: &EffectId,
    ) -> Result<Option<Completion>, StoreError> {
        Ok(self
            .read::<Completion>(&self.layout.effect_completion(effect)?)
            .await?
            .map(|stored| stored.value))
    }
    pub(super) async fn complete(
        &self,
        effect: &EffectId,
        completion: &Completion,
    ) -> Result<(), StoreError> {
        let key = self.layout.effect_completion(effect)?;
        match self.write(&key, None, completion, &key).await {
            Ok(true) => Ok(()),
            outcome => match self.completion(effect).await {
                Ok(Some(stored)) if stored == *completion => Ok(()),
                Ok(Some(_)) => Err(invalid("effect_completion_conflict")),
                _ => match outcome {
                    Err(error) => Err(after_write(error, &key)),
                    Ok(_) => Err(unknown(&key)),
                },
            },
        }
    }
    pub(super) async fn payload(
        &self,
        payload: &Payload,
    ) -> Result<serde_json::Value, StoreError> {
        match payload {
            Payload::Inline(value) => Ok(value.clone()),
            Payload::Ref(reference) => {
                if reference.key.len() > 1024 || reference.digest.0.len() > 256 {
                    return Err(invalid("output_reference"));
                }
                let bytes = self
                    .io(self.stores.blobs.get(reference))
                    .await?
                    .ok_or_else(|| unavailable("output_blob_missing"))?;
                if crate::archive::blob_reference(&reference.key, &bytes).digest != reference.digest {
                    return Err(invalid("output_blob_digest"));
                }
                let value: serde_json::Value =
                    serde_json::from_slice(&bytes).map_err(|_| invalid("output_blob_json"))?;
                Ok(value)
            }
        }
    }
    pub(super) async fn display(
        &self,
        content: &Payload,
        limits: &OutputLimits,
    ) -> Result<DisplaySnapshot, StoreError> {
        self.projection
            .validate_limits(limits)
            .map_err(|_| invalid("output_limits"))?;
        let snapshot = serde_json::from_value(
            self.payload(content).await?,
        )
        .map_err(|_| invalid("display_decode"))?;
        self.projection
            .fold_committed(&snapshot, &[], limits)
            .map_err(|_| invalid("display_validation"))
    }
    pub(super) async fn page(
        &self,
        key: &str,
        after: Option<EventId>,
        through: EventId,
    ) -> Result<Vec<OutputEvent>, StoreError> {
        Ok(self
            .stored_page(key, after, through)
            .await?
            .into_iter()
            .map(|stored| stored.event)
            .collect())
    }
    pub(super) async fn stored_page(
        &self,
        key: &str,
        after: Option<EventId>,
        through: EventId,
    ) -> Result<Vec<StoredOutput>, StoreError> {
        // The plugin takes a count, not a byte limit. Use the maximum admitted
        // wire event size to bound even its returned page before deserialization.
        let count = self
            .config
            .page_entries
            .min((self.config.page_bytes / MAX_ENTRY_BYTES).max(1) as u32);
        let page = self
            .io(async {
                self.stores
                    .streams
                    .range(
                        key,
                        after
                            .map(|id| Bound::Excluded(stream_id(id)))
                            .unwrap_or(Bound::Unbounded),
                        Bound::Included(stream_id(through)),
                        NonZeroU32::new(count).ok_or_else(|| invalid("output_page_count"))?,
                        false,
                    )
                    .await
                    .map_err(|e| crate::error::from_read(&e))
            })
            .await?;
        if page.entries.len() > count as usize {
            return Err(invalid("output_page_count"));
        }
        let mut bytes = 0usize;
        let mut previous = after;
        page.entries
            .into_iter()
            .map(|entry| {
                let encoded = entry
                    .fields
                    .get("event")
                    .ok_or_else(|| invalid("output_entry"))?;
                let source = entry
                    .fields
                    .get("effect")
                    .ok_or_else(|| invalid("output_entry_source"))?;
                if source.len() > 1024 {
                    return Err(invalid("output_entry_source"));
                }
                bytes = bytes
                    .checked_add(encoded.len() + source.len())
                    .ok_or_else(|| invalid("output_page_bytes"))?;
                if entry.fields.len() != 2
                    || encoded.len() > MAX_EVENT_BYTES
                    || bytes > self.config.page_bytes
                {
                    return Err(invalid("output_page_bytes"));
                }
                let event: OutputEvent =
                    serde_json::from_slice(encoded).map_err(|_| invalid("output_entry"))?;
                if event.id != event_id(entry.id)?
                    || event.id > through
                    || previous.is_some_and(|id| id >= event.id)
                {
                    return Err(invalid("output_page_order"));
                }
                previous = Some(event.id);
                let effect: EffectId =
                    serde_json::from_slice(source).map_err(|_| invalid("output_entry_source"))?;
                if effect.commit != event.id.commit
                    || !event.tid.0.starts_with(&format!("{}.", effect.sid.0))
                {
                    return Err(invalid("output_entry_source"));
                }
                Ok(StoredOutput { event, effect })
            })
            .collect()
    }
}

pub(super) fn bounded_bytes(value: &impl Serialize, maximum: usize) -> Result<Vec<u8>, StoreError> {
    struct Writer {
        bytes: Vec<u8>,
        maximum: usize,
    }
    impl Write for Writer {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            if bytes.len() > self.maximum.saturating_sub(self.bytes.len()) {
                return Err(std::io::Error::other("output encoding bound"));
            }
            self.bytes.extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut writer = Writer {
        bytes: Vec::new(),
        maximum,
    };
    serde_json::to_writer(&mut writer, value).map_err(|_| invalid("output_encoded_size"))?;
    Ok(writer.bytes)
}
pub(super) fn display_event(event: &OutputEvent) -> bool {
    !matches!(
        event.body,
        OutputBody::Artifact {
            class: OutputClass::Thinking,
            ..
        }
    )
}
