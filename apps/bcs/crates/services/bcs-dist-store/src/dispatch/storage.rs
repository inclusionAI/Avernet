use super::*;
impl StoreDispatch {
    pub(super) async fn io<T>(
        &self,
        f: impl Future<Output = Result<T, StoreError>>,
        write: bool,
        id: &str,
    ) -> Result<T, StoreError> {
        tokio::time::timeout(self.config.io_timeout, async {
            let _slot = self
                .io_slots
                .acquire()
                .await
                .map_err(|_| unavailable("dispatch_shutdown"))?;
            f.await
        })
        .await
        .map_err(|_| {
            if write {
                unknown(id)
            } else {
                unavailable("dispatch_read_timeout")
            }
        })?
    }
    pub(super) async fn read<T: DeserializeOwned>(
        &self,
        key: &str,
    ) -> Result<Option<Versioned<T>>, StoreError> {
        self.io(
            async {
                self.stores
                    .documents
                    .read_document(key)
                    .await
                    .map_err(|e| crate::error::from_read(&e))?
                    .map(|d| {
                        Ok(Versioned {
                            version: d.version,
                            value: decode_json(d.value)?,
                        })
                    })
                    .transpose()
            },
            false,
            key,
        )
        .await
    }
    pub(super) async fn write<T: Serialize>(
        &self,
        key: &str,
        expected: Option<Version>,
        value: &T,
    ) -> Result<bool, StoreError> {
        if bytes(value)? > 960 * 1024 {
            return Err(invalid("dispatch_document_capacity"));
        }
        let value = encode_json(value)?;
        self.io(
            async {
                let result = match expected {
                    Some(v) => self.stores.documents.replace_document(key, v, &value).await,
                    None => self.stores.documents.create_document(key, &value).await,
                }
                .map_err(|e| crate::error::from_write(&e, key))?;
                Ok(matches!(result, WriteOutcome::Applied { .. }))
            },
            true,
            key,
        )
        .await
    }
    pub(super) async fn document(
        &self,
        did: &Did,
    ) -> Result<Option<Versioned<Document>>, StoreError> {
        let doc = self.read::<Document>(&self.layout.disp(did)?).await?;
        if let Some(d) = &doc {
            format(d.value.schema_version)?;
            if d.value.view.spec.did != *did {
                return Err(invalid("dispatch_identity"));
            }
        }
        Ok(doc)
    }
    pub(super) async fn journal(
        &self,
        shard: ShardId,
    ) -> Result<(Option<Version>, Journal), StoreError> {
        match self
            .read::<Journal>(&self.layout.dispatch_journal(shard))
            .await?
        {
            Some(d) => {
                format(d.value.schema_version)?;
                Ok((Some(d.version), d.value))
            }
            None => Ok((None, Journal::default())),
        }
    }
    pub(super) async fn hint(&self, doc: &Document) -> Result<(), StoreError> {
        let shard = KeyLayout::shard_of(&doc.view.spec.sid)?;
        self.hint_due(shard, &doc.view.spec.did, doc.due).await
    }
    pub(super) async fn hint_due(
        &self,
        shard: ShardId,
        did: &Did,
        due: Option<TimestampMillis>,
    ) -> Result<(), StoreError> {
        let key = self.layout.sched(shard);
        self.io(
            async {
                match due {
                    Some(due) => {
                        self.stores
                            .index
                            .add(
                                &key,
                                &[ZSetEntry {
                                    member: did.0.clone(),
                                    score: due.0 as f64,
                                }],
                                ZAddMode::Upsert,
                            )
                            .await
                            .map_err(|e| crate::error::from_write(&e, &key))?;
                    }
                    None => {
                        self.stores
                            .index
                            .remove(&key, std::slice::from_ref(&did.0))
                            .await
                            .map_err(|e| crate::error::from_write(&e, &key))?;
                    }
                }
                Ok(())
            },
            true,
            &key,
        )
        .await
    }
    pub(super) async fn live(&self, instance: &Instance) -> Result<Liveness, StoreError> {
        self.io(
            self.stores.membership.liveness(instance),
            false,
            "dispatch_liveness",
        )
        .await
    }
}
