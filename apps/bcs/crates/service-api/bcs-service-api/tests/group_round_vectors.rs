#![allow(clippy::unwrap_used, reason = "contract fixtures")]
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::group_round::*;

#[test]
fn source_domains_are_distinct_and_length_delimited() {
    let scope=TaskScope { tenant:TenantId("ab".into()),principal:PrincipalId("c".into()) };
    let other=TaskScope { tenant:TenantId("a".into()),principal:PrincipalId("bc".into()) };
    let group=GroupId("g".into()); let message=RequestId("m".into());
    let strict=GroupRequestIdentity::strict(&scope,&group,&message);
    assert_ne!(strict.components(),GroupRequestIdentity::strict(&other,&group,&message).components());
    assert_eq!(strict.components()[0],"group_a2a:v1");
    assert_eq!(strict.components(),vec!["group_a2a:v1","ab","c","g","m"]);
    // Hash each UTF-8 component with a length prefix; never concatenate these
    // fields or reuse the legacy `sid` domain from the JSON-RPC path.
}

#[test]
fn projection_profile_rejects_unsupported_page_and_history_shapes() {
    let b=RoundBudgetProfile::v1();
    assert!(b.validate_projection(100,100).is_ok());
    assert!(b.validate_projection(101,0).is_err());
    assert!(b.validate_projection(1,101).is_err());
    assert!(b.validate_projection(0,0).is_err());
}

/// The candidate profile must pin the declared reference constants exactly:
/// Worker inbound boundary is the merged #4245 phase-one payload capacity and
/// the internal feed caps keep the production #4245 magnitudes.
#[test]
fn profile_v1_pins_the_merged_phase_one_and_feed_constants() {
    let b = RoundBudgetProfile::v1();
    assert_eq!(b.inbound_bytes, PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES);
    assert_eq!(b.feed_event_bytes, 128 * 1024);
    assert_eq!(b.feed_batch_bytes, 256 * 1024);
    assert!(b.max_page_rows <= 100);
}
