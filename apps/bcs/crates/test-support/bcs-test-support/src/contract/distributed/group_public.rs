//! Provider-independent public group contracts. Host fixtures control only
//! publication and the live transition between the two page reads.
use super::ContractResult;
use bcs_dist_model::{GroupId, TaskScope, TaskStatus, Tid, TimestampMillis};
use bcs_service_api::application::dist_gateway::{
    GatewayCaller, GatewayError, GroupDiscoveryService,
};
use bcs_service_api::port::repo::distributed::group_listing::{GroupIndexQuery, GroupListingPort};
use std::num::NonZeroU32;

pub async fn group_discovery_service_contract_tests(
    discovery: &dyn GroupDiscoveryService,
    caller: &GatewayCaller,
    group: &GroupId,
    unpublish: impl FnOnce(),
) -> ContractResult {
    let card = discovery.card(caller, group).await?;
    assert_eq!(&card.group, group);
    assert!(card.streaming);
    assert!(!card.push_notifications);
    unpublish();
    assert_eq!(
        discovery.card(caller, group).await,
        Err(GatewayError::NotFound)
    );
    Ok(())
}

/// Supply two submitted tasks, ordered by the cut, then change the second to working.
pub async fn group_listing_port_contract_tests(
    listing: &dyn GroupListingPort,
    scope: &TaskScope,
    group: &GroupId,
    ids: &[Tid; 2],
    now: TimestampMillis,
    change_second: impl Future<Output = ()>,
) -> ContractResult {
    let query = GroupIndexQuery {
        statuses: vec![TaskStatus::Submitted],
        limit: NonZeroU32::MIN,
        load_rows: true,
    };
    let first = listing.first_page(scope, group, &query, now).await?;
    assert_eq!(first.total, 2);
    assert_eq!(first.rows.len(), 1);
    assert_eq!(first.rows[0].tid, ids[0]);
    let cursor = first.next.ok_or("expected signed continuation")?;
    change_second.await;
    let second = listing
        .continue_page(scope, group, &cursor, true, now)
        .await?;
    assert_eq!((second.cut, second.total), (first.cut, first.total));
    assert_eq!(second.rows.len(), 1);
    assert_eq!(second.rows[0].tid, ids[1]);
    assert_eq!(
        second.rows[0]
            .view
            .as_ref()
            .ok_or("expected pinned view")?
            .content
            .status,
        TaskStatus::Submitted
    );
    assert!(second.next.is_none());
    let fresh = listing.first_page(scope, group, &query, now).await?;
    assert_eq!(fresh.total, 1);
    Ok(())
}
