//! Key names, version helpers, digests and codecs.
#![allow(clippy::unwrap_used, clippy::expect_used, reason = "test assertions")]

mod common;

use bcs_dist_model::*;
use bcs_dist_store::codec::{
    AdmitRecord, decode_ev_entry, decode_inbox_entry, encode_ev_entry, encode_inbox_entry,
    encode_wake_entry, ev_entry_tid, event_id, stream_id,
};
use bcs_store_api::stream::{StreamEntry, StreamId};
use std::num::NonZeroU32;
use bcs_dist_store::error::{NEED_CONTRACT, from_read, from_write, need_contract};
use bcs_dist_store::identity::{digest_fields, digest_json};
use bcs_dist_store::layout::{HeartbeatCopy, KeyLayout};
use bcs_dist_store::version;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::{StoreError as PluginError, Version};
use serde_json::json;

fn layout() -> KeyLayout {
    KeyLayout::new("c01", "main").unwrap()
}

#[test]
fn key_names_match_the_catalogue() {
    let l = layout();
    let sid = Sid("s0042.7f3a9c".into());
    let shard = KeyLayout::shard_of(&sid).unwrap();
    assert_eq!(shard, ShardId(42));
    assert_eq!(l.inbox(&sid).unwrap(), "bcn:v1:c01:main:s0042:inbox:s0042.7f3a9c");
    assert_eq!(l.ckpt(&sid).unwrap(), "bcn:v1:c01:main:s0042:ckpt:s0042.7f3a9c");
    assert_eq!(l.spec(&sid).unwrap(), "bcn:v1:c01:main:s0042:spec:s0042.7f3a9c");
    assert_eq!(l.ev(&sid).unwrap(), "bcn:v1:c01:main:s0042:ev:s0042.7f3a9c");
    assert_eq!(l.session_tasks(&sid).unwrap(), "bcn:v1:c01:main:s0042:tasks:s0042.7f3a9c");
    assert_eq!(
        l.task(&Tid("s0042.7f3a9c.b21e".into())).unwrap(),
        "bcn:v1:c01:main:s0042:task:s0042.7f3a9c.b21e"
    );
    assert_eq!(
        l.disp(&Did("s0042.7f3a9c.b21e.n2.a1".into())).unwrap(),
        "bcn:v1:c01:main:s0042:disp:s0042.7f3a9c.b21e.n2.a1"
    );
    assert_eq!(
        l.admit(&sid, &InputId("adm-9c41e7".into())).unwrap(),
        "bcn:v1:c01:main:s0042:admit:adm-9c41e7"
    );
    assert_eq!(l.owner(shard), "bcn:v1:c01:main:s0042:owner");
    assert_eq!(l.active(shard), "bcn:v1:c01:main:s0042:active");
    assert_eq!(l.wake(shard), "bcn:v1:c01:main:s0042:wake");
    assert_eq!(l.sched(shard), "bcn:v1:c01:main:s0042:sched");
    assert_eq!(
        l.command(&PoolId("a2a".into()), 0).unwrap(),
        "bcn:v1:cmd:c01:main:a2a:p0"
    );
    let conn = Instance {
        node: "conn-03".into(),
        incarnation: "1794370950".into(),
    };
    assert_eq!(l.held(&conn).unwrap(), "bcn:v1:held:c01:main:conn-03:1794370950");
    assert_eq!(
        l.heartbeat(HeartbeatCopy::B, &conn).unwrap(),
        "bcn:v1:hb:c01:main:b:conn-03:1794370950"
    );
    assert_eq!(
        l.member("connector", &conn).unwrap(),
        "bcn:v1:c01:main:member:connector:conn-03:1794370950"
    );
    assert_eq!(l.members("connector").unwrap(), "bcn:v1:c01:main:members:connector");
    assert_eq!(KeyLayout::shard_text(ShardId(7)), "s0007");
    assert_eq!(KeyLayout::shard_text(ShardId(12345)), "s12345");
}

#[test]
fn no_key_contains_a_hash_tag() {
    let l = layout();
    for bad in ["s0042.{x}", "s0042.a b", "s0042.a\n", ""] {
        assert!(l.inbox(&Sid(bad.into())).is_err(), "{bad:?}");
    }
    assert!(l.admit(&Sid("s0042.a".into()), &InputId("x{y}".into())).is_err());
    assert!(KeyLayout::new("c{01}", "main").is_err());
    assert!(KeyLayout::new("c01", "ma:in").is_err());
}

#[test]
fn ids_must_carry_a_shard() {
    for bad in ["7f3a9c", "x0042.7f3a9c", "s.7f3a9c", "s0042", "s99999999999.a"] {
        assert!(KeyLayout::shard_of(&Sid(bad.into())).is_err(), "{bad:?}");
    }
}

#[test]
fn version_helpers_follow_the_bands() {
    assert_eq!(version::CREATE, Version(1));
    assert_eq!(
        version::commit(WorkerEpoch(14), CommitSeq(12)).unwrap(),
        Version(14_000_000_012)
    );
    assert_eq!(version::seal(WorkerEpoch(14)).unwrap(), Version(14_000_000_000));
    assert_eq!(version::claim(HolderEpoch(2)).unwrap(), Version(2_000_000_000));
    assert_eq!(version::next(Version(1_000_000_003)).unwrap(), Version(1_000_000_004));
    assert_eq!(version::band_of(Version(14_000_000_012)), 14);

    assert!(version::commit(WorkerEpoch(14), CommitSeq(1_000_000_000)).is_err());
    assert!(version::commit(WorkerEpoch(u64::MAX), CommitSeq(1)).is_err());
    assert!(version::seal(WorkerEpoch(0)).is_err());
    assert!(version::claim(HolderEpoch(0)).is_err());
    assert!(version::next(Version(1_999_999_999)).is_err());
    assert!(version::next(Version(u64::MAX)).is_err());
}

#[test]
fn errors_keep_unknown_apart_and_hide_provider_text() {
    let unknown = PluginError::OutcomeUnknown("secret detail".into());
    assert_eq!(
        from_write(&unknown, "adm-1"),
        StoreError::OutcomeUnknown {
            operation_id: "adm-1".into()
        }
    );
    assert!(matches!(from_read(&unknown), StoreError::NotApplied { .. }));
    let invalid = from_write(&PluginError::InvalidInput("secret detail".into()), "adm-1");
    assert!(matches!(invalid, StoreError::Invalid { ref code } if !code.contains("secret")));
    assert_eq!(
        need_contract(),
        StoreError::NotApplied {
            code: NEED_CONTRACT.into()
        }
    );
}

#[test]
fn digests_are_stable() {
    assert_ne!(digest_fields(&[b"ab", b"c"]), digest_fields(&[b"a", b"bc"]));
    let a = json!({"b": 1, "a": {"y": [1, 2], "x": null}});
    let b = json!({"a": {"x": null, "y": [1, 2]}, "b": 1});
    assert_eq!(digest_json(&a), digest_json(&b));
    assert_ne!(digest_json(&a), digest_json(&json!({"b": 2})));
    assert!(digest_json(&a).0.starts_with("sha256:"));
    assert_eq!(digest_json(&a).0.len(), "sha256:".len() + 64);
}

#[test]
fn codecs_round_trip() {
    let record = AdmitRecord {
        digest: Digest("sha256:3f1a".into()),
        sid: Sid(common::SID.into()),
        tid: Tid(common::TID.into()),
    };
    assert_eq!(AdmitRecord::decode(&record.encode().unwrap()).unwrap(), record);
    assert_eq!(
        String::from_utf8(record.encode().unwrap()).unwrap(),
        r#"{"digest":"sha256:3f1a","sid":"s0042.7f3a9c","tid":"s0042.7f3a9c.b21e"}"#
    );

    let input = common::user_input("adm-9c41e7", "hello");
    let fields = encode_inbox_entry(&input).unwrap();
    assert_eq!(fields["kind"], b"user");
    assert_eq!(fields["input_id"], b"adm-9c41e7");
    assert_eq!(decode_inbox_entry(&input.sid, &fields).unwrap(), input);

    assert_eq!(encode_wake_entry(&input.sid)["sid"], common::SID.as_bytes());
}

#[test]
fn unsupported_schema_versions_are_rejected() {
    use bcs_dist_store::codec::decode_json;
    let input = common::user_input("adm-9c41e7", "hello");
    let mut fields = encode_inbox_entry(&input).unwrap();
    fields.insert("v".into(), b"2".to_vec());
    assert_eq!(
        decode_inbox_entry(&input.sid, &fields),
        Err(StoreError::UnsupportedFormat { format: 2 })
    );

    let mut newer = input.clone();
    newer.schema_version = 2;
    assert_eq!(
        encode_inbox_entry(&newer),
        Err(StoreError::UnsupportedFormat { format: 2 })
    );

    let mut spec = serde_json::to_value(common::spec("grp-1")).unwrap();
    spec["schema_version"] = json!(2);
    assert_eq!(
        decode_json::<SessionSpec>(spec),
        Err(StoreError::UnsupportedFormat { format: 2 })
    );
    let current = serde_json::to_value(common::task()).unwrap();
    assert!(decode_json::<TaskSnapshot>(current).is_ok());
}

fn status_event() -> OutputEvent {
    OutputEvent {
        id: EventId {
            commit: CommitSeq(12),
            index: NonZeroU32::new(1).unwrap(),
        },
        tid: Tid(common::TID.into()),
        body: OutputBody::Status {
            status: TaskStatus::Working,
            interactions: vec![],
        },
    }
}

#[test]
fn ev_entry_round_trips_and_pins_its_fields() {
    let event = status_event();
    let (id, fields) = encode_ev_entry(&event).unwrap();
    assert_eq!(id, StreamId { major: 12, minor: 1 });
    assert_eq!(fields.keys().map(String::as_str).collect::<Vec<_>>(), ["body", "kind", "tid", "v"]);
    assert_eq!(fields["v"], b"1");
    assert_eq!(fields["tid"], common::TID.as_bytes());
    assert_eq!(fields["kind"], b"status");
    assert_eq!(
        String::from_utf8(fields["body"].clone()).unwrap(),
        r#"{"kind":"status","status":"working","interactions":[]}"#
    );
    let entry = StreamEntry { id, fields };
    assert_eq!(ev_entry_tid(&entry), Some(Tid(common::TID.into())));
    assert_eq!(decode_ev_entry(&entry).unwrap(), event);
    assert_eq!(event_id(id), Some(event.id));
    assert_eq!(stream_id(event.id), id);
}

#[test]
fn ev_entries_a_reader_must_skip_are_errors() {
    let (id, fields) = encode_ev_entry(&status_event()).unwrap();
    let mut newer = fields.clone();
    newer.insert("v".into(), b"2".to_vec());
    assert_eq!(
        decode_ev_entry(&StreamEntry { id, fields: newer }),
        Err(StoreError::UnsupportedFormat { format: 2 })
    );
    let mut broken = fields.clone();
    broken.insert("body".into(), b"{".to_vec());
    assert!(matches!(
        decode_ev_entry(&StreamEntry { id, fields: broken }),
        Err(StoreError::Invalid { .. })
    ));
    // An index of 0 is not a valid event id.
    let zero = StreamId { major: 12, minor: 0 };
    assert_eq!(event_id(zero), None);
    assert!(decode_ev_entry(&StreamEntry { id: zero, fields }).is_err());
}
