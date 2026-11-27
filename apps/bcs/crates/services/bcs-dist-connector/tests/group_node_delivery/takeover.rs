//! Dedupe-proof vectors: a takeover (or an unknown outcome) may rely on the
//! target's dedupe only inside the recorded proof — window live, same
//! domain, same semantics, valid configuration unchanged, current
//! authorization verified. Anything else adds no send attempts.
use crate::harness::*;
use bcs_dist_connector::rules::DistConnectorRules;
use bcs_dist_model::*;
use bcs_service_api::core::dist_connector::{DedupeRecheck, DistConnectorCoreService, UncertainSend};
use rig::*;

fn recheck<'a>(
    recorded: &'a SendProof,
    current: &'a SendProof,
    now: Option<TimestampMillis>,
    domain: Option<&'a str>,
) -> DedupeRecheck<'a> {
    DedupeRecheck {
        recorded: Some(recorded),
        current,
        now,
        resolved_domain: domain,
    }
}

#[test]
fn takeover_rechecks_dedupe_window_and_domain() {
    let rules = DistConnectorRules;
    let spec = node_spec("n2", 1);
    let config = engine_config();
    let request_id = RequestId(format!("{}:start", spec.did.0));
    let mut recorded = written_proof(&spec, &rules, &config, &request_id);
    // Hypothetical trusted evidence tests the predicate in isolation; no
    // production resolution currently supplies these facts to the actor.
    recorded.dedupe_domain = "verified-test-domain".into();
    recorded.guarantee_deadline = TimestampMillis(NOW + 1_000);
    let caps = caps();
    // In window, same domain, same semantics, same valid configuration.
    assert_eq!(
        rules.uncertain_start(&caps, &recheck(&recorded, &recorded, Some(TimestampMillis(NOW)), Some(&recorded.dedupe_domain))),
        UncertainSend::Resend
    );
    // From here on every failure of the recheck must stop dedupe-based
    // resends entirely: a queryable target may still be *asked* for the
    // request — the answer is never a second send — and one without any
    // query stays unresolved. These legs bind each recheck first because
    // they borrow the proofs they compare.
    fn never_resends(
        rules: &DistConnectorRules,
        caps: &DownstreamCapabilities,
        recheck: &DedupeRecheck<'_>,
    ) -> bool {
        matches!(
            rules.uncertain_start(caps, recheck),
            UncertainSend::Resend
        )
    }
    // The guarantee window has ended.
    let expired = recheck(&recorded, &recorded, Some(recorded.guarantee_deadline), Some(&recorded.dedupe_domain));
    assert!(!never_resends(&rules, &caps, &expired), "the window ended");
    // The clock is not trusted.
    let timeless = recheck(&recorded, &recorded, None, Some(&recorded.dedupe_domain));
    assert!(!never_resends(&rules, &caps, &timeless), "clock unknown");
    // The current resolution is not verified, so the authorization is not.
    let unresolved = recheck(&recorded, &recorded, Some(TimestampMillis(NOW)), None);
    assert!(!never_resends(&rules, &caps, &unresolved), "resolution unverified");
    // The same credential ref now resolves to another domain.
    let mut elsewhere = recorded.clone();
    elsewhere.dedupe_domain = format!("{}#rotated", recorded.dedupe_domain);
    let rotated = recheck(&recorded, &elsewhere, Some(TimestampMillis(NOW)), Some(&elsewhere.dedupe_domain));
    assert!(!never_resends(&rules, &caps, &rotated), "a domain change stops dedupe-based resends");
    // The valid send configuration changed.
    let mut reconfigured = recorded.clone();
    reconfigured.config_digest = Digest("sha256:other-config".into());
    let reconfigured_proof = recheck(&recorded, &reconfigured, Some(TimestampMillis(NOW)), Some(&recorded.dedupe_domain));
    assert!(!never_resends(&rules, &caps, &reconfigured_proof), "configuration changed");
    // The request semantics drifted.
    let mut other_body = recorded.clone();
    other_body.body_digest = Digest("sha256:other-body".into());
    let drifted = recheck(&recorded, &other_body, Some(TimestampMillis(NOW)), Some(&recorded.dedupe_domain));
    assert!(!never_resends(&rules, &caps, &drifted), "semantics drifted");
    // No durable proof: nothing to rely on.
    let unproved = DedupeRecheck {
        recorded: None,
        current: &recorded,
        now: Some(TimestampMillis(NOW)),
        resolved_domain: Some(&recorded.dedupe_domain),
    };
    assert!(!never_resends(&rules, &caps, &unproved), "no proof recorded");
    // Without the negotiated dedupe there is nothing to fall back on: the
    // upheld proof cannot produce a resend, and with no query to ask, the
    // call stays unresolved however valid the proof looks.
    let strict_source = DownstreamCapabilities {
        query_by_request: false,
        persistent_dedupe: false,
        ..caps.clone()
    };
    let strict = DedupeRecheck { recorded: None, current: &recorded, now: Some(TimestampMillis(NOW)), resolved_domain: Some(&recorded.dedupe_domain) };
    assert_eq!(
        rules.uncertain_start(&strict_source, &strict),
        UncertainSend::Unresolved,
        "no dedupe negotiated and no query: unresolved however valid the proof"
    );
    // Declared idempotency alone (no negotiated persistent dedupe) does not
    // allow a resend, but the request can still be asked for by id.
    let no_dedupe = DownstreamCapabilities { persistent_dedupe: false, ..caps.clone() };
    assert_eq!(
        rules.uncertain_start(&no_dedupe, &recheck(&recorded, &recorded, Some(TimestampMillis(NOW)), Some(&recorded.dedupe_domain))),
        UncertainSend::AskAgent
    );
    // Inputs follow the same rules under their own proof.
    let mut input_proof = rules.send_proof(
        &spec,
        ProofOperation::Input { seq: InputSeq(1), request_id: RequestId(format!("{}:in:1", spec.did.0)) },
        b"fixture input body",
        config.send_fingerprint(&caps),
    );
    input_proof.dedupe_domain = recorded.dedupe_domain.clone();
    input_proof.guarantee_deadline = recorded.guarantee_deadline;
    // The recorded START proof never vouches for an input resend: an input
    // attempt re-checks against a recorded input proof of its own.
    assert_eq!(
        rules.uncertain_input(&caps, &recheck(&input_proof, &input_proof, Some(TimestampMillis(NOW)), Some(&input_proof.dedupe_domain))),
        UncertainSend::Resend
    );
    assert_eq!(
        rules.uncertain_input(&no_dedupe, &recheck(&input_proof, &input_proof, Some(TimestampMillis(NOW)), Some(&input_proof.dedupe_domain))),
        UncertainSend::Unresolved
    );
    assert!(
        never_resends(
            &rules,
            &caps,
            &recheck(&recorded, &input_proof, Some(TimestampMillis(NOW)), Some(&input_proof.dedupe_domain)),
        ) == false,
        "a start proof never vouches for an input resend"
    );
}
