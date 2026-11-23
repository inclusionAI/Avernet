//! One injected governor for every scanner using a Worker's storage pool.
use bcs_service_api::port::repo::distributed::{budget::*, common::StoreError};
use std::sync::{Arc, Mutex};
use tokio::{
    sync::{OwnedSemaphorePermit, Semaphore},
    time::Instant,
};
const UNIT: u64 = 1_000_000_000;
#[derive(Debug, Clone)]
pub struct ScanBudgetConfig {
    pub pool_size: usize,
    pub io_slots: usize,
    pub logs_per_second: u32,
    pub sessions_per_second: u32,
    pub burst: u32,
}
impl Default for ScanBudgetConfig {
    fn default() -> Self {
        Self {
            pool_size: 32,
            io_slots: 8,
            logs_per_second: 1000,
            sessions_per_second: 200,
            burst: 128,
        }
    }
}
pub struct SharedScanBudget {
    profile: ScanBudgetProfile,
    io: Arc<Semaphore>,
    tokens: Mutex<Tokens>,
}
struct Tokens {
    at: Instant,
    logs: [u64; 4],
    sessions: [u64; 4],
}
struct Permit {
    _permit: OwnedSemaphorePermit,
}
impl SharedScanBudget {
    pub fn new(config: ScanBudgetConfig) -> Result<Self, StoreError> {
        if config.pool_size < 4
            || config.io_slots == 0
            || config.io_slots > 8
            || config.io_slots > config.pool_size / 4
            || !(4..=1000).contains(&config.logs_per_second)
            || !config.logs_per_second.is_multiple_of(4)
            || !(4..=200).contains(&config.sessions_per_second)
            || !config.sessions_per_second.is_multiple_of(4)
            || !(4..=128).contains(&config.burst)
            || !config.burst.is_multiple_of(4)
        {
            return Err(StoreError::Invalid {
                code: "scan_budget_config".into(),
            });
        }
        let capacity = u64::from(config.burst / 4) * UNIT;
        Ok(Self {
            io: Arc::new(Semaphore::new(config.io_slots)),
            profile: ScanBudgetProfile {
                identity: uuid::Uuid::new_v4().to_string(),
                pool_size: config.pool_size,
                io_slots: config.io_slots,
                logs_per_second: config.logs_per_second,
                sessions_per_second: config.sessions_per_second,
                burst: config.burst,
            },
            tokens: Mutex::new(Tokens {
                at: Instant::now(),
                logs: [capacity; 4],
                sessions: [capacity; 4],
            }),
        })
    }
}
impl ScanBudgetPort for SharedScanBudget {
    fn io_in_use(&self) -> Option<usize> {
        Some(
            self.profile
                .io_slots
                .saturating_sub(self.io.available_permits()),
        )
    }
    fn profile(&self) -> ScanBudgetProfile {
        self.profile.clone()
    }
    fn try_io(&self) -> Option<ScanIoPermit> {
        self.io
            .clone()
            .try_acquire_owned()
            .ok()
            .map(|permit| Box::new(Permit { _permit: permit }) as ScanIoPermit)
    }
    fn claim(&self, class: ScanClass, logs: u32, sessions: u32) -> Result<bool, StoreError> {
        if logs > self.profile.burst || sessions > self.profile.burst {
            return Ok(false);
        }
        let class = match class {
            ScanClass::Repair => 0,
            ScanClass::Discovery => 1,
            ScanClass::Dispatch => 2,
        };
        let mut tokens = self.tokens.lock().map_err(|_| StoreError::Invalid {
            code: "scan_budget_poisoned".into(),
        })?;
        let now = Instant::now();
        let elapsed = now
            .duration_since(tokens.at)
            .as_nanos()
            .min(u128::from(u64::MAX)) as u64;
        tokens.at = now;
        let capacity = u64::from(self.profile.burst / 4) * UNIT;
        for bucket in &mut tokens.logs {
            *bucket = bucket
                .saturating_add(elapsed.saturating_mul(u64::from(self.profile.logs_per_second / 4)))
                .min(capacity);
        }
        for bucket in &mut tokens.sessions {
            *bucket = bucket
                .saturating_add(
                    elapsed.saturating_mul(u64::from(self.profile.sessions_per_second / 4)),
                )
                .min(capacity);
        }
        let logs = u64::from(logs) * UNIT;
        let sessions = u64::from(sessions) * UNIT;
        if tokens.logs[class] + tokens.logs[3] < logs
            || tokens.sessions[class] + tokens.sessions[3] < sessions
        {
            return Ok(false);
        }
        take(&mut tokens.logs, class, logs);
        take(&mut tokens.sessions, class, sessions);
        Ok(true)
    }
}
fn take(buckets: &mut [u64; 4], class: usize, amount: u64) {
    let own = buckets[class].min(amount);
    buckets[class] -= own;
    buckets[3] -= amount - own;
}

pub(crate) fn validate_profile(
    profile: &ScanBudgetProfile,
    pool_size: usize,
) -> Result<(), StoreError> {
    if profile.identity.is_empty()
        || profile.identity.len() > 256
        || profile.pool_size != pool_size
        || pool_size < 4
        || profile.io_slots == 0
        || profile.io_slots > 8
        || profile.io_slots > pool_size / 4
        || !(4..=1000).contains(&profile.logs_per_second)
        || !profile.logs_per_second.is_multiple_of(4)
        || !(4..=200).contains(&profile.sessions_per_second)
        || !profile.sessions_per_second.is_multiple_of(4)
        || !(4..=128).contains(&profile.burst)
        || !profile.burst.is_multiple_of(4)
    {
        return Err(StoreError::Invalid {
            code: "shared_scan_budget_profile".into(),
        });
    }
    Ok(())
}
