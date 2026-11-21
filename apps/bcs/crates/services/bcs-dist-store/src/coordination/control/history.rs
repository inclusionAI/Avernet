use super::*;
#[derive(Serialize, Deserialize)]
struct RetainedIsolation {
    revision: u64,
    record: IsolationRecord,
}
impl CoordinationStore {
    pub(super) async fn control_get_isolation(
        &self,
        sid: &Sid,
        id: &RequestId,
    ) -> Result<Option<IsolationRecord>, StoreError> {
        validate_text(&id.0)?;
        budget(&id.0, async {
            if let Some(header) = self.control_header(sid).await?
                && let Some(record) = isolation(&header.snapshot.control.mode)
                && record.operation_id == *id
            {
                return Ok(Some(record.clone()));
            }
            Ok(self
                .read::<RetainedIsolation>(&self.layout.control_isolation(sid, id)?)
                .await?
                .map(|stored| stored.value.record))
        })
        .await
    }
    pub(super) async fn retain_isolation(&self, header: &Header) -> Result<(), StoreError> {
        let Some(record) = isolation(&header.snapshot.control.mode) else {
            return Ok(());
        };
        let key = self
            .layout
            .control_isolation(&header.snapshot.owner.sid, &record.operation_id)?;
        let value = RetainedIsolation {
            revision: header.snapshot.control.revision,
            record: record.clone(),
        };
        for retry in 0..8 {
            backoff(retry).await?;
            let prior = self.read::<RetainedIsolation>(&key).await?;
            if let Some(prior) = &prior {
                if prior.value.record.operation_id != record.operation_id
                    || prior.value.record.reason != record.reason
                    || prior.value.record.head != record.head
                    || prior.value.record.failed_attempt != record.failed_attempt
                {
                    return Err(invalid("isolation_history_conflict"));
                }
                if prior.value.revision > value.revision || prior.value.record == value.record {
                    return Ok(());
                }
                if prior.value.revision == value.revision
                    || (prior.value.record.closure.is_some()
                        && prior.value.record.closure != record.closure)
                    || (prior.value.record.completion.is_some()
                        && prior.value.record.completion != record.completion)
                {
                    return Err(invalid("isolation_history_conflict"));
                }
            }
            if self
                .write(
                    &key,
                    prior.map(|p| p.version),
                    &value,
                    &record.operation_id.0,
                )
                .await?
                .is_some()
            {
                return Ok(());
            }
        }
        Err(unknown(&record.operation_id.0))
    }
    pub(super) async fn retain_recovery(
        &self,
        authorization: &RecoveryAuthorization,
    ) -> Result<bool, StoreError> {
        let key = self
            .layout
            .control_recovery(&authorization.guard.owner.sid, &authorization.request_id)?;
        if let Some(prior) = self.read::<RecoveryAuthorization>(&key).await? {
            return Ok(prior.value == *authorization);
        }
        if self
            .write(&key, None, authorization, &authorization.request_id.0)
            .await?
            .is_some()
        {
            return Ok(true);
        }
        Ok(self
            .read::<RecoveryAuthorization>(&key)
            .await?
            .is_some_and(|prior| prior.value == *authorization))
    }
}
