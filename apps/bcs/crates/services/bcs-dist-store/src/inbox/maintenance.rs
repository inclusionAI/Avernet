//! Bounded takeover enumeration without a shard-wide admission journal.
//!
//! Admission documents are addressable by scoped InputId and are repaired by
//! the caller retrying that identity. Worker takeover discovers Sessions from
//! the durable `active` index and then reads schedule/checkpoint state. The
//! old sequence journal repair/discovery lanes are intentionally unsupported in
//! this MVP.
use super::{StoreInboxProducer, storage::*};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{admission::*, budget::ScanClass, common::*};
use bcs_store_api::zset::{ZSetEntry, ZSetRange};
use std::ops::Bound;

impl StoreInboxProducer {
    pub(super) async fn history_page(
        &self,
        shard: ShardId,
        page: PageRequest,
    ) -> Result<Page<Sid>, StoreError> {
        if page.limit.get() > 128 {
            return Err(invalid("page_limit"));
        }
        if !self.inner.scan_budget.claim(ScanClass::Discovery, page.limit.get(), page.limit.get())? {
            return Err(StoreError::NotApplied { code: "scan_budget".into() });
        }
        let after = page
            .after
            .map(|p| {
                serde_json::from_str::<(f64, String)>(&p.0)
                    .map(|(score, member)| ZSetEntry { score, member })
                    .map_err(|_| invalid("pending_cursor"))
            })
            .transpose()?;
        let _permit = self.io_permit().await?;
        let entries = tokio::time::timeout(
            std::time::Duration::from_millis(100),
            self.inner.zset.range(
                &self.inner.layout.active(shard),
                &ZSetRange {
                    min: Bound::Unbounded,
                    max: Bound::Unbounded,
                    after,
                    limit: page.limit,
                },
            ),
        )
        .await
        .map_err(|_| unknown("pending_scan"))?
        .map_err(|e| crate::error::from_read(&e))?;
        let items = entries
            .iter()
            .filter_map(|entry| {
                let sid = Sid(entry.member.clone());
                (crate::layout::KeyLayout::shard_of(&sid).ok() == Some(shard)).then_some(sid)
            })
            .collect::<Vec<_>>();
        let next = entries
            .last()
            .filter(|_| entries.len() == page.limit.get() as usize)
            .map(|entry| {
                serde_json::to_string(&(entry.score, &entry.member))
                    .map(PageToken)
                    .map_err(|_| invalid("pending_cursor"))
            })
            .transpose()?;
        Ok(Page { items, next })
    }
}

#[async_trait]
impl AdmissionMaintenancePort for StoreInboxProducer {
    async fn repair(&self, _shard: ShardId) -> Result<AdmissionMaintenance, StoreError> {
        Ok(AdmissionMaintenance {
            round_complete: true,
            ..AdmissionMaintenance::default()
        })
    }

    async fn discover(&self, _shard: ShardId) -> Result<AdmissionMaintenance, StoreError> {
        Ok(AdmissionMaintenance {
            round_complete: true,
            ..AdmissionMaintenance::default()
        })
    }
}
