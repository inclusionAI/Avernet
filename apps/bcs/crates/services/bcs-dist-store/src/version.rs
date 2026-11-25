//! Legacy greater-than write versions for independently validated GT paths.
//! Worker coordination and admission do NOT use this epoch encoding: they use
//! DocumentCas with private versions, exact EQ and create-if-absent.
//!
//! The version space is split into bands of 10^9 per epoch: a Worker commits at
//! `epoch × 10^9 + commit_seq`, a seal jumps to `epoch × 10^9`, a Connector claim
//! to `holder_epoch × 10^9`, and every other write uses `read + 1` inside the band
//! it read. Written with `WriteCondition::Newer` ("greater wins, equal rejected").

use bcs_dist_model::{CommitSeq, HolderEpoch, WorkerEpoch};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::Version;

/// Size of one epoch band.
pub const BAND: u64 = 1_000_000_000;

/// Legacy first version for callers requiring retained GT history.
/// This is not the admission create condition and does not itself prevent
/// recreation after deletion; that guarantee requires a matching capability.
pub const CREATE: Version = Version(1);

/// A Worker commit: `epoch × 10^9 + commit_seq`, with `commit_seq < 10^9`.
pub fn commit(epoch: WorkerEpoch, seq: CommitSeq) -> Result<Version, StoreError> {
    if seq.0 >= BAND {
        return Err(invalid("commit_seq_out_of_band"));
    }
    band_start(epoch.0)?
        .checked_add(seq.0)
        .map(Version)
        .ok_or_else(|| invalid("version_overflow"))
}

/// A per-session seal by a new Worker: `epoch × 10^9`.
pub fn seal(epoch: WorkerEpoch) -> Result<Version, StoreError> {
    band_start(epoch.0).map(Version)
}

/// A Connector claim of a dispatch: `new_holder_epoch × 10^9`.
pub fn claim(new_holder_epoch: HolderEpoch) -> Result<Version, StoreError> {
    band_start(new_holder_epoch.0).map(Version)
}

/// Every other conditional write: the version read plus one, which must stay in
/// the band it was read in (otherwise it would look like a claim or a seal).
pub fn next(read: Version) -> Result<Version, StoreError> {
    let next = read
        .0
        .checked_add(1)
        .ok_or_else(|| invalid("version_overflow"))?;
    if next / BAND != read.0 / BAND {
        return Err(invalid("version_leaves_band"));
    }
    Ok(Version(next))
}

/// The epoch band a stored version belongs to.
pub fn band_of(version: Version) -> u64 {
    version.0 / BAND
}

fn band_start(epoch: u64) -> Result<u64, StoreError> {
    if epoch == 0 {
        return Err(invalid("epoch_zero"));
    }
    epoch
        .checked_mul(BAND)
        .ok_or_else(|| invalid("version_overflow"))
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid {
        code: code.to_owned(),
    }
}
