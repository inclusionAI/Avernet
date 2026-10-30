//! JWT-profile conformance for the default `CallerIdentityPlugin`
//! (spec `2026-09-16-caller-identity-session-container` §10.13.4/§10.13.7).
//!
//! Deterministic: the port takes explicit `now_ms` everywhere, so no clock
//! doubles are needed. The Ed25519 keys below are generated test fixtures
//! committed for hermetic runs; they are not production material and grant
//! nothing outside this crate's tests.

use bcs_caller_identity_jwt::{
    JwtCallerIdentityConfig, JwtCallerIdentityConfigError, JwtCallerIdentityPlugin, TrustedPublicKey,
};
use bcs_caller_identity_api::{
    AuthenticatedHumanCallerIngress, CallerExecutionPurpose, CallerExecutionToken,
    CallerProofFormat, CallerSubjectKind, CallerSubjectRef, CallerSubjectRefType,
    HumanCallerAuthSource,
};
use bcs_caller_identity_api::HumanActor;
use bcs_caller_identity_api::{
    CallerDelegationBounds, CallerIdentityError, CallerIdentityPlugin, CallerIsolationConstraint,
    CallerProofScope, CallerRunBinding, EstablishCallerSubject, EstablishedCallerSubject,
    ExpectedCallerExecution, IssueCallerExecution, IssueCallerExecutionSource, VerifyCallerExecution,
};

const KID_A: &str = "test-key-a";
const KID_B: &str = "test-key-b";
const KID_X: &str = "test-key-x";
const PRIVATE_KEY_A: &str = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIJLx28TZRXq9av+DXCadGa9MFANFALD61QgkJ4ZvD2YM\n-----END PRIVATE KEY-----";
const PUBLIC_KEY_A: &str = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEABdQgXwZ5e+KJRyNu5FNYaj1mRtE/BzLJIzaB+RZkIgc=\n-----END PUBLIC KEY-----";
const PRIVATE_KEY_B: &str = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIJsIIiku31bvdL1Gud2FmB/vHrYb7BgeA0nhYz87rPqP\n-----END PRIVATE KEY-----";
const PUBLIC_KEY_B: &str = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA/frWim6T3X9EEivraQHB+fIiwpmXsDeI1VWn7/5RnvM=\n-----END PUBLIC KEY-----";

const PSEUDONYM_KEY: &[u8] = b"test-pseudonym-key-0123456789abcdef";
const PSEUDONYM_KEY_ALT: &[u8] = b"other-pseudonym-key-0123456789abcde";

const NOW: i64 = 1_780_000_000_000;
const MAX_TTL_SECONDS: u64 = 300;

fn config() -> JwtCallerIdentityConfig {
    JwtCallerIdentityConfig {
        issuer: "bcs-caller".to_string(),
        signing_kid: KID_A.to_string(),
        signing_key_pem: PRIVATE_KEY_A.to_string(),
        public_keys: vec![
            TrustedPublicKey { kid: KID_A.to_string(), public_pem: PUBLIC_KEY_A.to_string() },
            TrustedPublicKey { kid: KID_B.to_string(), public_pem: PUBLIC_KEY_B.to_string() },
        ],
        pseudonym_key: PSEUDONYM_KEY.to_vec(),
        max_ttl_seconds: MAX_TTL_SECONDS,
    }
}

fn make_plugin() -> JwtCallerIdentityPlugin {
    JwtCallerIdentityPlugin::new(config()).unwrap()
}

fn ingress_for(actor_id: &str, staff_no: &str) -> AuthenticatedHumanCallerIngress {
    AuthenticatedHumanCallerIngress::new(
        HumanActor { actor_id: actor_id.to_string(), staff_no: staff_no.to_string() },
        HumanCallerAuthSource::HttpAuth,
        NOW,
        "prod",
    )
    .unwrap()
}

async fn established_with(plugin: &JwtCallerIdentityPlugin) -> EstablishedCallerSubject {
    plugin
        .establish_subject(EstablishCallerSubject {
            ingress: ingress_for("human_alice", "S12345"),
            now_ms: NOW,
        })
        .await
        .unwrap()
}

fn input_run(attempt: u32) -> CallerRunBinding {
    CallerRunBinding {
        run_id: "run_ab".to_string(),
        root_run_id: "run_ha".to_string(),
        parent_run_id: None,
        message_turn_id: Some("msg_001".to_string()),
        attempt,
    }
}

fn input_scope() -> CallerProofScope {
    CallerProofScope {
        env: "prod".to_string(),
        target_bot_id: "bot_b".to_string(),
        group_id: None,
        session_id: "session_001".to_string(),
    }
}

fn clone_ref(subject: &CallerSubjectRef) -> CallerSubjectRef {
    CallerSubjectRef::new(
        CallerSubjectKind::Human,
        CallerSubjectRefType::BcsEphemeral,
        subject.expose_ref().to_string(),
        subject.expires_at_ms(),
    )
    .unwrap()
}

fn clone_token(token: &CallerExecutionToken) -> CallerExecutionToken {
    CallerExecutionToken::new(
        token.format(),
        token.expose_token().to_string(),
        token.expires_at_ms(),
    )
    .unwrap()
}

fn issue_input(
    subject: &CallerSubjectRef,
    purpose: CallerExecutionPurpose,
) -> IssueCallerExecution<'static> {
    IssueCallerExecution {
        staff_no: bcs_caller_identity_api::CallerOpaqueRef::new("S1"),
        agent_code: Some("agent-driver".into()),
        sandbox_container_id: None,
        subject: clone_ref(subject),
        run: input_run(1),
        scope: input_scope(),
        delegation: CallerDelegationBounds { depth: 0, max_depth: 2 },
        purpose,
        not_after_ms: NOW + 60_000,
        isolation: None,
        source: IssueCallerExecutionSource::HumanOriginHandle,
        now_ms: NOW,
    }
}

fn expectation(
    scope: CallerProofScope,
    run: CallerRunBinding,
    purpose: CallerExecutionPurpose,
    isolation: Option<CallerIsolationConstraint>,
) -> ExpectedCallerExecution {
    ExpectedCallerExecution { scope, run, purpose, isolation }
}

async fn issue(
    plugin: &JwtCallerIdentityPlugin,
    subject: &CallerSubjectRef,
    purpose: CallerExecutionPurpose,
) -> CallerExecutionToken {
    plugin.issue_execution(issue_input(subject, purpose)).await.unwrap()
}

#[tokio::test]
async fn next_hop_execution_is_exchanged_from_a_bcs_owned_parent_token() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;

    let mut parent_input = issue_input(
        &established.subject_ref,
        CallerExecutionPurpose::Execution,
    );
    parent_input.run = CallerRunBinding {
        run_id: "run_manager".to_string(),
        root_run_id: "run_root".to_string(),
        parent_run_id: None,
        message_turn_id: Some("msg_001".to_string()),
        attempt: 1,
    };
    let parent = plugin.issue_execution(parent_input).await.unwrap();

    let child_subject = clone_ref(&established.subject_ref);
    let parent_run = CallerRunBinding {
        run_id: "run_manager".to_string(),
        root_run_id: "run_root".to_string(),
        parent_run_id: None,
        message_turn_id: Some("msg_001".to_string()),
        attempt: 1,
    };
    let child_run = CallerRunBinding {
        run_id: "run_worker".to_string(),
        parent_run_id: Some("run_manager".to_string()),
        ..parent_run.clone()
    };
    let scope = input_scope();
    let input = IssueCallerExecution {
        staff_no: bcs_caller_identity_api::CallerOpaqueRef::new("S1"),
        agent_code: Some("agent-driver".into()),
        sandbox_container_id: None,
        subject: child_subject,
        run: child_run.clone(),
        scope: scope.clone(),
        delegation: CallerDelegationBounds { depth: 1, max_depth: 2 },
        purpose: CallerExecutionPurpose::Execution,
        not_after_ms: NOW + 60_000,
        isolation: None,
        source: IssueCallerExecutionSource::ParentExecutionTokenRef {
            token: &parent,
            parent_run_id: "run_manager",
            parent_attempt: 1,
        },
        now_ms: NOW,
    };
    let child = plugin.issue_execution(input).await.unwrap();

    let verified = plugin
        .verify_execution(VerifyCallerExecution {
            token: child,
            expected: expectation(scope, child_run, CallerExecutionPurpose::Execution, None),
            now_ms: NOW,
        })
        .await
        .unwrap();
    assert_eq!(verified.subject_ref.expose_ref(), established.subject_ref.expose_ref());
}


#[tokio::test]
async fn establish_subject_is_a_stable_env_scoped_pseudonym() {
    let plugin = make_plugin();
    let s1 = established_with(&plugin).await;
    // Same actor + env => same stable pseudonym, regardless of display data.
    let s2 = plugin
        .establish_subject(EstablishCallerSubject {
            ingress: ingress_for("human_alice", "S99999"),
            now_ms: NOW + 60_000,
        })
        .await
        .unwrap();
    assert_eq!(s1.subject_ref.expose_ref(), s2.subject_ref.expose_ref());
    assert_eq!(s1.subject_ref.kind(), CallerSubjectKind::Human);
    assert_eq!(s1.subject_ref.ref_type(), CallerSubjectRefType::BcsEphemeral);

    // Different env or actor => different pseudonym.
    let other_env = plugin
        .establish_subject(EstablishCallerSubject {
            ingress: AuthenticatedHumanCallerIngress::new(
                HumanActor { actor_id: "human_alice".into(), staff_no: "S12345".into() },
                HumanCallerAuthSource::HttpAuth,
                NOW,
                "test",
            )
            .unwrap(),
            now_ms: NOW,
        })
        .await
        .unwrap();
    assert_ne!(s1.subject_ref.expose_ref(), other_env.subject_ref.expose_ref());

    let other_actor = plugin
        .establish_subject(EstablishCallerSubject {
            ingress: ingress_for("human_bob", "B1"),
            now_ms: NOW,
        })
        .await
        .unwrap();
    assert_ne!(s1.subject_ref.expose_ref(), other_actor.subject_ref.expose_ref());

    // The pseudonym carries neither the raw actor id nor the staff number.
    let raw = s1.subject_ref.expose_ref();
    assert!(!raw.contains("human_alice") && !raw.contains("S12345"));
}

#[tokio::test]
async fn pseudonyms_do_not_collide_across_independent_key_sets() {
    let mut other_config = config();
    other_config.pseudonym_key = PSEUDONYM_KEY_ALT.to_vec();
    let other = JwtCallerIdentityPlugin::new(other_config).unwrap();
    let a = established_with(&make_plugin()).await;
    let b = other
        .establish_subject(EstablishCallerSubject {
            ingress: ingress_for("human_alice", "S1"),
            now_ms: NOW,
        })
        .await
        .unwrap();
    assert_ne!(a.subject_ref.expose_ref(), b.subject_ref.expose_ref());
}

#[tokio::test]
async fn subject_lifetime_follows_the_advisory_ttl() {
    let plugin = make_plugin();
    // No identity window: the established lifetime is always the profile
    // TTL advisory, never derived from an authentication window.
    let established = plugin
        .establish_subject(EstablishCallerSubject {
            ingress: ingress_for("human_alice", "S1"),
            now_ms: NOW,
        })
        .await
        .unwrap();
    assert_eq!(established.expires_at_ms, NOW + (MAX_TTL_SECONDS as i64) * 1000);

    // ... and it never shrinks relative to the issuance clock either.
    let again = plugin
        .establish_subject(EstablishCallerSubject {
            ingress: ingress_for("human_alice", "S1"),
            now_ms: NOW + 60_000,
        })
        .await
        .unwrap();
    assert_eq!(again.expires_at_ms, NOW + 60_000 + (MAX_TTL_SECONDS as i64) * 1000);
}

#[tokio::test]
async fn issued_token_and_subject_never_leak_through_debug() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    let token = issue(&plugin, &established.subject_ref, CallerExecutionPurpose::Execution).await;

    let subject_debug = format!("{:?}", established);
    let token_debug = format!("{:?}", token);
    for debug in [&subject_debug, &token_debug] {
        assert!(!debug.contains("S12345"), "staff principal leaked: {debug}");
        assert!(debug.contains("<redacted>"), "redaction not explicit: {debug}");
    }
    assert!(!subject_debug.contains(established.subject_ref.expose_ref()));
    assert!(!token_debug.contains(token.expose_token()));
    assert!(!subject_debug.contains(established.subject_proof.expose_token()));
}

#[tokio::test]
async fn issued_token_round_trips_against_exact_expectations() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    let token = issue(&plugin, &established.subject_ref, CallerExecutionPurpose::Execution).await;
    assert_eq!(token.format(), CallerProofFormat::Jwt);

    let verified = plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&token),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW + 1_000,
        })
        .await
        .unwrap();
    assert_eq!(verified.subject_ref.expose_ref(), established.subject_ref.expose_ref());
    assert_eq!(verified.purpose, CallerExecutionPurpose::Execution);
    assert_eq!(verified.expires_at_ms, token.expires_at_ms());
}

#[tokio::test]
async fn token_lifetime_is_the_advisory_ttl_regardless_of_deadlines() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    // The token's expiry is the issuance clock plus the profile TTL — the
    // request/subject deadline inputs no longer participate.
    let input = IssueCallerExecution {
        not_after_ms: 1_780_000_250_999,
        ..issue_input(&established.subject_ref, CallerExecutionPurpose::Execution)
    };
    let issued = plugin.issue_execution(input).await.unwrap();
    assert_eq!(issued.expires_at_ms(), NOW + (MAX_TTL_SECONDS as i64) * 1000);

    // A tighter request deadline does not shrink the advisory lifetime.
    let input = IssueCallerExecution {
        not_after_ms: NOW + 60_000,
        ..issue_input(&established.subject_ref, CallerExecutionPurpose::Execution)
    };
    let issued = plugin.issue_execution(input).await.unwrap();
    assert_eq!(issued.expires_at_ms(), NOW + (MAX_TTL_SECONDS as i64) * 1000);

    // Verification no longer rejects on the boundary: the expiry value is
    // advisory, so verifying at and past the token's own `exp` succeeds.
    let proof = clone_token(&issued);
    let at_boundary = plugin
        .verify_execution(VerifyCallerExecution {
            token: proof,
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: issued.expires_at_ms(),
        })
        .await
        .unwrap();
    assert_eq!(at_boundary.expires_at_ms, issued.expires_at_ms());
    let late_proof = clone_token(&issued);
    assert!(plugin
        .verify_execution(VerifyCallerExecution {
            token: late_proof,
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: issued.expires_at_ms() + 60_000,
        })
        .await
        .is_ok());
}

#[tokio::test]
async fn verification_rejects_a_tampered_token() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    let token = issue(&plugin, &established.subject_ref, CallerExecutionPurpose::Execution).await;

    let mut forged = token.expose_token().to_string();
    let last_dot = forged.rfind('.').unwrap();
    let flip_at = last_dot - 2;
    let original = forged.as_bytes()[flip_at];
    forged.replace_range(
        flip_at..flip_at + 1,
        if original == b'A' { "B" } else { "A" },
    );
    let res = plugin
        .verify_execution(VerifyCallerExecution {
            token: CallerExecutionToken::new(CallerProofFormat::Jwt, forged, token.expires_at_ms()).unwrap(),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::TokenInvalid);
}

#[tokio::test]
async fn verification_rejects_unknown_or_untrusted_keys() {
    // Token signed by a plugin whose kid is not in the verifier trust set.
    let mut unknown_kid_config = config();
    unknown_kid_config.signing_kid = KID_X.to_string();
    unknown_kid_config.signing_key_pem = PRIVATE_KEY_B.to_string();
    let unknown_kid = JwtCallerIdentityPlugin::new(unknown_kid_config).unwrap();
    let foreign_subject = established_with(&unknown_kid).await;
    let foreign = issue(&unknown_kid, &foreign_subject.subject_ref, CallerExecutionPurpose::Execution).await;

    let plugin = make_plugin();
    let res = plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&foreign),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::TokenInvalid);

    // Token signed with B while the verifier trusts only A.
    let mut b_signing = config();
    b_signing.signing_kid = KID_B.to_string();
    b_signing.signing_key_pem = PRIVATE_KEY_B.to_string();
    let by_b = JwtCallerIdentityPlugin::new(b_signing).unwrap();
    let b_subject = established_with(&by_b).await;
    let b_token = issue(&by_b, &b_subject.subject_ref, CallerExecutionPurpose::Execution).await;

    let mut a_only_config = config();
    a_only_config.public_keys = vec![TrustedPublicKey { kid: KID_A.to_string(), public_pem: PUBLIC_KEY_A.to_string() }];
    let a_only = JwtCallerIdentityPlugin::new(a_only_config).unwrap();
    let res = a_only
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&b_token),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::TokenInvalid);
}

#[tokio::test]
async fn trusted_key_rotation_accepts_both_kids() {
    // Verifier trusts {A, B}; current signing key is B.
    let mut rotated = config();
    rotated.signing_kid = KID_B.to_string();
    rotated.signing_key_pem = PRIVATE_KEY_B.to_string();
    let plugin = JwtCallerIdentityPlugin::new(rotated).unwrap();
    let established = established_with(&plugin).await;
    let token = issue(&plugin, &established.subject_ref, CallerExecutionPurpose::Execution).await;

    plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&token),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW + 1_000,
        })
        .await
        .unwrap();

    // The same token is accepted by another instance sharing the trust set.
    let peer = make_plugin();
    peer.verify_execution(VerifyCallerExecution {
        token: clone_token(&token),
        expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
        now_ms: NOW + 1_000,
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn verification_rejects_algorithm_confusion() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    let token = issue(&plugin, &established.subject_ref, CallerExecutionPurpose::Execution).await;

    // Forge an HS256-framed token over the genuine payload. Whatever secret
    // an attacker uses, the EdDSA-only profile must reject the token by its
    // declared algorithm before any signature work.
    let (_, payload) = token.expose_token().split_once('.').unwrap();
    let header = b64url(r#"{"alg":"HS256","typ":"JWT","kid":"test-key-a"}"#);
    let forged = format!("{header}.{payload}.c2lnbmF0dXJl");
    let res = plugin
        .verify_execution(VerifyCallerExecution {
            token: CallerExecutionToken::new(CallerProofFormat::Jwt, forged, token.expires_at_ms()).unwrap(),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::TokenInvalid);

    // The symmetric-token family is rejected too.
    let header = b64url(r#"{"alg":"none","typ":"JWT"}"#);
    let forged = format!("{header}.{payload}.");
    let res = plugin
        .verify_execution(VerifyCallerExecution {
            token: CallerExecutionToken::new(CallerProofFormat::Jwt, forged, token.expires_at_ms()).unwrap(),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::TokenInvalid);
}

#[tokio::test]
async fn delegation_only_purpose_never_satisfies_execution() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    let token = issue(&plugin, &established.subject_ref, CallerExecutionPurpose::DelegationOnly).await;

    // The matching purpose verifies.
    plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&token),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::DelegationOnly, None),
            now_ms: NOW + 1_000,
        })
        .await
        .unwrap();

    // An execution expectation rejects a lineage-only proof.
    let res = plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&token),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW + 1_000,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::PurposeMismatch);
}

#[tokio::test]
async fn verification_rejects_audience_scope_and_run_mismatches() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    let token = issue(&plugin, &established.subject_ref, CallerExecutionPurpose::Execution).await;
    let purpose = CallerExecutionPurpose::Execution;

    // Audience: another bot may never consume this proof.
    let res = plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&token),
            expected: expectation(
                CallerProofScope { env: "prod".into(), target_bot_id: "bot_c".into(), group_id: None, session_id: "session_001".into() },
                input_run(1),
                purpose,
                None,
            ),
            now_ms: NOW,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::AudienceMismatch);

    // Scope: wrong session, env or group.
    for scope in [
        CallerProofScope { env: "prod".into(), target_bot_id: "bot_b".into(), group_id: None, session_id: "other".into() },
        CallerProofScope { env: "test".into(), target_bot_id: "bot_b".into(), group_id: None, session_id: "session_001".into() },
        CallerProofScope { env: "prod".into(), target_bot_id: "bot_b".into(), group_id: Some("g".into()), session_id: "session_001".into() },
    ] {
        let res = plugin
            .verify_execution(VerifyCallerExecution {
                token: clone_token(&token),
                expected: expectation(scope, input_run(1), purpose, None),
                now_ms: NOW,
            })
            .await;
        assert_eq!(res.unwrap_err(), CallerIdentityError::ScopeMismatch);
    }

    // Run relation: wrong run, attempt, parent or turn.
    for run in [
        CallerRunBinding { run_id: "other_run".into(), ..input_run(1) },
        CallerRunBinding { attempt: 2, ..input_run(1) },
        CallerRunBinding { parent_run_id: Some("run_x".into()), ..input_run(1) },
        CallerRunBinding { message_turn_id: None, ..input_run(1) },
    ] {
        let res = plugin
            .verify_execution(VerifyCallerExecution {
                token: clone_token(&token),
                expected: expectation(input_scope(), run, purpose, None),
                now_ms: NOW,
            })
            .await;
        assert_eq!(res.unwrap_err(), CallerIdentityError::TokenInvalid, "run relation must be rejected");
    }
}

#[tokio::test]
async fn binding_semantics_are_exact_not_wildcards() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    let input = IssueCallerExecution {
        isolation: Some(CallerIsolationConstraint { binding_ref: "bind_1".into(), generation: Some(3) }),
        ..issue_input(&established.subject_ref, CallerExecutionPurpose::Execution)
    };
    let token = plugin.issue_execution(input).await.unwrap();

    // Exact expectation passes.
    plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&token),
            expected: expectation(
                input_scope(),
                input_run(1),
                CallerExecutionPurpose::Execution,
                Some(CallerIsolationConstraint { binding_ref: "bind_1".into(), generation: Some(3) }),
            ),
            now_ms: NOW + 1_000,
        })
        .await
        .unwrap();

    // A different reference, generation, or an absent expectation is rejected.
    for isolation in [
        Some(CallerIsolationConstraint { binding_ref: "bind_2".into(), generation: Some(3) }),
        Some(CallerIsolationConstraint { binding_ref: "bind_1".into(), generation: Some(4) }),
        Some(CallerIsolationConstraint { binding_ref: "bind_1".into(), generation: None }),
        None,
    ] {
        let res = plugin
            .verify_execution(VerifyCallerExecution {
                token: clone_token(&token),
                expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, isolation),
                now_ms: NOW + 1_000,
            })
            .await;
        assert_eq!(res.unwrap_err(), CallerIdentityError::IsolationUnsupported);
    }

    // An unbound proof never satisfies a bound expectation either.
    let unbound = issue(&plugin, &established.subject_ref, CallerExecutionPurpose::Execution).await;
    let res = plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&unbound),
            expected: expectation(
                input_scope(),
                input_run(1),
                CallerExecutionPurpose::Execution,
                Some(CallerIsolationConstraint { binding_ref: "bind_1".into(), generation: None }),
            ),
            now_ms: NOW + 1_000,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::IsolationUnsupported);
}

#[tokio::test]
async fn issuance_rejects_malformed_inputs() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;

    // attempt == 0 is never auto-corrected.
    let res = plugin
        .issue_execution(IssueCallerExecution {
            run: input_run(0),
            ..issue_input(&established.subject_ref, CallerExecutionPurpose::Execution)
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::InvalidRequest);

    // Subject-establishment is not an executable purpose.
    let res = plugin
        .issue_execution(issue_input(&established.subject_ref, CallerExecutionPurpose::SubjectEstablishment))
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::InvalidRequest);

    // An already-elapsed advisory on the subject is NOT a refusal —
    // identity carries no window.
    let stale_subject = CallerSubjectRef::new(
        CallerSubjectKind::Human,
        CallerSubjectRefType::BcsEphemeral,
        "opaque".into(),
        NOW,
    )
    .unwrap();
    assert!(plugin
        .issue_execution(issue_input(&stale_subject, CallerExecutionPurpose::Execution))
        .await
        .is_ok());

    // Out-of-range delegation arithmetic.
    let res = plugin
        .issue_execution(IssueCallerExecution {
            delegation: CallerDelegationBounds { depth: 3, max_depth: 2 },
            ..issue_input(&established.subject_ref, CallerExecutionPurpose::Execution)
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::InvalidRequest);
}

#[tokio::test]
async fn verify_rejects_a_subject_proof_used_as_execution_proof() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    let res = plugin
        .verify_execution(VerifyCallerExecution {
            token: clone_token(&established.subject_proof),
            expected: expectation(input_scope(), input_run(1), CallerExecutionPurpose::Execution, None),
            now_ms: NOW,
        })
        .await;
    assert_eq!(res.unwrap_err(), CallerIdentityError::TokenInvalid);
}

#[tokio::test]
async fn config_validation_fails_closed() {
    // Empty issuer/kid, bad PEMs, empty trust set, weak pseudonym key and
    // zero TTL all refuse to build a plugin — no working-default fallback.
    let modifiers: [fn(&mut JwtCallerIdentityConfig); 6] = [
        |c| c.issuer = String::new(),
        |c| c.signing_kid = String::new(),
        |c| c.signing_key_pem = "not a pem".into(),
        |c| c.public_keys = vec![],
        |c| c.pseudonym_key = b"short".to_vec(),
        |c| c.max_ttl_seconds = 0,
    ];
    for modifier in modifiers {
        let mut bad = config();
        modifier(&mut bad);
        assert!(JwtCallerIdentityPlugin::new(bad).is_err(), "config must fail closed");
    }

    // A bad public key entry in the trust set names the offending key.
    let mut bad = config();
    bad.public_keys[0].public_pem = "garbage".into();
    assert!(matches!(
        JwtCallerIdentityPlugin::new(bad).err().unwrap(),
        JwtCallerIdentityConfigError::InvalidPublicKey { .. }
    ));
}

/// Minimal unpadded URL-safe base64 for forged-test headers only.
fn b64url(data: &str) -> String {
    const TABLE: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let bytes = data.as_bytes();
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(TABLE[(n >> 18 & 63) as usize] as char);
        out.push(TABLE[(n >> 12 & 63) as usize] as char);
        if chunk.len() > 1 {
            out.push(TABLE[(n >> 6 & 63) as usize] as char);
        }
        if chunk.len() > 2 {
            out.push(TABLE[(n & 63) as usize] as char);
        }
    }
    out
}
#[tokio::test]
async fn conformance_caller_identity_plugin() {
    let plugin = make_plugin();
    let established = established_with(&plugin).await;
    bcs_test_support::contract::plugin::caller_identity::caller_identity_plugin_contract_tests(
        &plugin, || issue_input(&established.subject_ref, CallerExecutionPurpose::Execution),
    ).await;
}
