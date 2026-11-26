//! Round-identity parsing of dispatch ids (plan T6): only the authoritative
//! `<tid>.fsm:<generation>:<node>:<attempt>` shape carries round authority —
//! every other shape, including old `fsm:{tid}:...` forms, carries none.
use bcs_dist_encoding::connector::round_of;
use bcs_dist_model::Did;

#[test]
fn the_authoritative_did_yields_its_round() {
    assert_eq!(
        round_of(&Did("s0042.7f3a9c.b21e.fsm:1:n2:1".into())).as_deref(),
        Some("s0042.7f3a9c.b21e.fsm:1")
    );
    assert_eq!(
        round_of(&Did("plan.task-9.fsm:7:research:3".into())).as_deref(),
        Some("plan.task-9.fsm:7")
    );
    // The round is the attempt-independent identity of the node's attempts
    // within one generation: renumbering the node or the attempt never
    // changes it.
    assert_eq!(
        round_of(&Did("t.fsm:2:n2:1".into())),
        round_of(&Did("t.fsm:2:n9:4".into()))
    );
}

#[test]
fn every_other_did_shape_carries_no_round() {
    // The plan's own old form is invalid by ruling 1.
    assert_eq!(round_of(&Did("fsm:s0042.7f3a9c.b21e:1:n2:1".into())), None);
    // Opaque ids from before the FSM plane.
    assert_eq!(round_of(&Did("s0042.7f3a9c.b21e.n2.a1".into())), None);
    // Any missing or extra segment.
    assert_eq!(round_of(&Did("t.fsm:1:n2".into())), None);
    assert_eq!(round_of(&Did("t.fsm:1:n2:1:extra".into())), None);
    // An empty generation, node or attempt is not an attempt identity.
    assert_eq!(round_of(&Did("t.fsm::n2:1".into())), None);
    assert_eq!(round_of(&Did("t.fsm:1::1".into())), None);
    assert_eq!(round_of(&Did("t.fsm:1:n2:".into())), None);
}
