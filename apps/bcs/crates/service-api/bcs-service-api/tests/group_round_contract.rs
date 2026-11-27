#![allow(clippy::unwrap_used, reason = "contract assertions")]
use bcs_dist_model::*;
use bcs_service_api::core::dist_runner::RunnerInput;
use bcs_service_api::port::repo::distributed::group_round::*;
use bcs_test_support::contract::distributed::group_round::read_round_payload;
use std::io::{Cursor, Read};

#[test]
fn typed_start_preserves_strict_round_identity() {
    let input: RoundInput = serde_json::from_value(serde_json::json!({
        "schema_version":1,"source":"group_a2a",
        "key":{"scope":{"tenant":"t","principal":"p"},"group":"g","sid":"s","tid":"a","generation":1},
        "ordered_parts":[{"kind":"text","text":"hello"}],
        "workflow_ref":{"definition_id":"f","revision":"1","digest":"d"},
        "history_cut":{"key":"h","digest":"hd"},"execution_deadline":100
    })).unwrap();
    let event = RunnerInput::GroupStart { input: input.clone() };
    match event {
        RunnerInput::GroupStart { input: recovered } => assert_eq!(recovered, input),
        _ => panic!("wrong start kind"),
    }
}

#[test]
fn revoked_read_and_unpublished_send_fail_closed() {
    let grants = GroupRoundGrants { published: false, invoke: true, read: true, cancel: false };
    assert!(!grants.allows(GroupRoundOperation::New));
    assert!(grants.allows(GroupRoundOperation::Replay));
    assert!(grants.allows(GroupRoundOperation::Read));
    assert!(!grants.allows(GroupRoundOperation::Cancel));
    let revoked = GroupRoundGrants { read: false, ..grants };
    assert!(!revoked.allows(GroupRoundOperation::Replay));
    assert!(!revoked.allows(GroupRoundOperation::Subscribe));
}

struct Counting { inner: Cursor<Vec<u8>>, read: usize }
impl Read for Counting {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let n=self.inner.read(buf)?; self.read+=n; Ok(n)
    }
}
/// Chunked response stream: yields at most `avail` total bytes. The oversized
/// intermediate is never fully materialized before the bounded read stops.
struct Endless { avail: usize, read: usize }
impl Read for Endless {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let n = buf.len().min(self.avail);
        if n > 0 { buf[..n].fill(b'x'); self.avail -= n; self.read += n; }
        Ok(n)
    }
}
#[test]
fn oversized_intermediate_result_is_bounded_failure() {
    let budget = RoundBudgetProfile::v1();
    let mut input = Counting { inner: Cursor::new(vec![b'x'; 9*1024*1024]), read:0 };
    assert!(read_round_payload(&mut input, budget.inbound_bytes).is_err());
    assert_eq!(input.read, budget.inbound_bytes + 1);
    // A small candidate final output cannot excuse the oversized intermediate.
    assert!(read_round_payload(&mut Cursor::new(vec![b'y';1024]), budget.public_frame_bytes).is_ok());
}

/// 入站 B−1/B/B+1 at the merged phase-one constant: 9 MiB intermediate
/// rejects at exactly 8 MiB+1 read; the boundary accepts B−1 and B whole and
/// stops the stream at B+1 without loading any oversized object.
#[test]
fn inbound_boundary_b_minus_1_b_b_plus_1_at_fixed_phase_one_constant() {
    let boundary = PHASE_ONE_MAX_INPUT_PAYLOAD_BYTES;
    for (avail, fits) in [(boundary - 1, true), (boundary, true), (boundary + 1, false)] {
        let mut source = Endless { avail, read: 0 };
        match read_round_payload(&mut source, boundary) {
            Ok(bytes) => {
                assert!(fits, "avail {avail} must not fit");
                assert_eq!(bytes.len(), avail);
            }
            Err(_) => assert!(!fits, "avail {avail} must fit"),
        }
        assert_eq!(source.read, if fits { avail } else { boundary + 1 });
    }
}
#[test]
fn inbound_boundary_does_not_silently_truncate() {
    for n in [7,8,9] {
        let mut source=Cursor::new(vec![b'x';n]);
        let value=read_round_payload(&mut source,8);
        if n<=8 { assert_eq!(value.unwrap(),vec![b'x';n]); }
        else { assert!(value.is_err()); }
    }
}
