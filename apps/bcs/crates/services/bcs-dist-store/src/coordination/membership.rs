use super::*;
use async_trait::async_trait;
use bcs_store_api::{lease::*, zset::*};
use std::ops::Bound;
fn role_name(role: RuntimeRole) -> &'static str {
    match role {
        RuntimeRole::Worker => "worker",
        RuntimeRole::Gateway => "gateway",
        RuntimeRole::Connector => "connector",
    }
}
impl CoordinationStore {
    fn lease_key(&self, instance: &Instance) -> Result<String, StoreError> {
        self.layout.member("instance", instance)
    }
    async fn update_member(
        &self,
        lease: &MembershipLease,
        ttl: Option<Duration>,
        draining: bool,
    ) -> Result<Renewal, StoreError> {
        let key = self.lease_key(&lease.instance)?;
        for _ in 0..RETRIES {
            let LeaseState::Alive(old) = self
                .leases
                .read_lease(&key)
                .await
                .map_err(|e| error::from_read(&e))?
            else {
                return Ok(Renewal::Lost);
            };
            let mut record: MemberRecord =
                serde_json::from_slice(&old.value).map_err(|_| invalid("member_format"))?;
            if record.instance != lease.instance {
                return Err(invalid("member_identity"));
            }
            record.draining |= draining;
            let value = serde_json::to_vec(&record).map_err(|_| invalid("member_encode"))?;
            match self
                .leases
                .update_lease(&key, old.version, &value, ttl)
                .await
                .map_err(|e| error::from_write(&e, &lease.instance.incarnation))?
            {
                LeaseWrite::Applied(_) => return Ok(Renewal::Renewed),
                LeaseWrite::Terminated => return Ok(Renewal::Lost),
                LeaseWrite::ConditionNotMet => continue,
            }
        }
        Err(not_applied("member_contention"))
    }
}
#[async_trait]
impl MembershipPort for CoordinationStore {
    async fn register(
        &self,
        instance: Instance,
        role: RuntimeRole,
        control_address: String,
        ttl: Duration,
    ) -> Result<MembershipLease, StoreError> {
        let key = self.lease_key(&instance)?;
        if ttl.is_zero()
            || !(control_address.starts_with("http://") || control_address.starts_with("https://"))
            || control_address.contains('@')
            || control_address.chars().any(char::is_whitespace)
        {
            return Err(invalid("registration"));
        }
        let record = MemberRecord {
            instance: instance.clone(),
            role,
            control_address,
            draining: false,
        };
        let value = serde_json::to_vec(&record).map_err(|_| invalid("member_encode"))?;
        let member = serde_json::to_string(&instance).map_err(|_| invalid("member_encode"))?;
        // Register discovery first. An orphan index item is harmless; a live
        // registration without an index entry would be undiscoverable.
        self.index
            .add(
                &self.layout.members(role_name(role))?,
                &[ZSetEntry { member, score: 0.0 }],
                ZAddMode::InsertOnly,
            )
            .await
            .map_err(|e| error::from_write(&e, &instance.incarnation))?;
        match self
            .leases
            .register_lease(&key, &value, ttl)
            .await
            .map_err(|e| error::from_write(&e, &instance.incarnation))?
        {
            LeaseRegistration::Active(_) => Ok(MembershipLease { instance }),
            LeaseRegistration::ContentConflict => Err(invalid("registration_content_conflict")),
            LeaseRegistration::Terminated => Err(not_applied("registration_terminated")),
        }
    }
    async fn renew(&self, lease: &MembershipLease, ttl: Duration) -> Result<Renewal, StoreError> {
        self.update_member(lease, Some(ttl), false).await
    }
    async fn set_draining(&self, lease: &MembershipLease) -> Result<Renewal, StoreError> {
        self.update_member(lease, None, true).await
    }
    async fn retire(&self, lease: &MembershipLease) -> Result<(), StoreError> {
        self.leases
            .retire_lease(&self.lease_key(&lease.instance)?)
            .await
            .map_err(|e| error::from_write(&e, &lease.instance.incarnation))
    }
    async fn liveness(&self, instance: &Instance) -> Result<Liveness, StoreError> {
        match self.leases.read_lease(&self.lease_key(instance)?).await {
            Ok(LeaseState::Alive(_)) => Ok(Liveness::Alive),
            Ok(LeaseState::Terminated) => Ok(Liveness::Dead),
            Ok(LeaseState::Absent) | Err(_) => Ok(Liveness::Unknown),
        }
    }
    async fn members(
        &self,
        role: RuntimeRole,
        page: PageRequest,
    ) -> Result<Page<MemberRecord>, StoreError> {
        let entries = self
            .index
            .range(
                &self.layout.members(role_name(role))?,
                &ZSetRange {
                    min: Bound::Unbounded,
                    max: Bound::Unbounded,
                    after: page.after.map(|s| ZSetEntry {
                        member: s.0,
                        score: 0.0,
                    }),
                    limit: page.limit,
                },
            )
            .await
            .map_err(|e| error::from_read(&e))?;
        let next = if entries.len() == page.limit.get() as usize {
            entries.last().map(|e| PageToken(e.member.clone()))
        } else {
            None
        };
        let mut items = Vec::new();
        for entry in entries {
            let instance: Instance =
                serde_json::from_str(&entry.member).map_err(|_| invalid("member_index"))?;
            if let LeaseState::Alive(value) = self
                .leases
                .read_lease(&self.lease_key(&instance)?)
                .await
                .map_err(|e| error::from_read(&e))?
            {
                let record: MemberRecord =
                    serde_json::from_slice(&value.value).map_err(|_| invalid("member_format"))?;
                if record.instance != instance {
                    return Err(invalid("member_identity"));
                }
                if record.role != role {
                    continue;
                }
                if !record.draining {
                    items.push(record)
                }
            }
        }
        Ok(Page { items, next })
    }
}
