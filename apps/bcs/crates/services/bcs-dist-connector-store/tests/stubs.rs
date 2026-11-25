//! Delayed contracts fail explicitly with `need_contract_not_implemented`.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "tests")]

use bcs_dist_connector_store::delayed;
use bcs_dist_model::*;
use bcs_dist_store::error::NEED_CONTRACT;
use bcs_service_api::port::repo::distributed::common::{PageRequest, StoreError};
use std::num::NonZeroU32;

fn conn() -> Instance {
    Instance { node: "conn-03".into(), incarnation: "1794370950000".into() }
}

#[tokio::test]
async fn repost_held_needs_contract_d3() {
    let page = PageRequest { after: None, limit: NonZeroU32::MIN };
    assert_eq!(
        delayed::repost_held(&conn(), page).await,
        Err(StoreError::NotApplied { code: NEED_CONTRACT.into() })
    );
    assert_eq!(NEED_CONTRACT, "need_contract_not_implemented");
}

#[tokio::test]
async fn release_needs_contract_d8() {
    let holder = Holder { instance: conn(), epoch: HolderEpoch(1) };
    assert_eq!(
        delayed::release(&Did("s0042.7f3a9c.b21e.n2.a1".into()), &holder).await,
        Err(StoreError::NotApplied { code: NEED_CONTRACT.into() })
    );
}
