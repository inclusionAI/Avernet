use super::{SCHEMA_VERSION, invalid, supported};
use bcs_dist_model::{CommitSeq, EventId, OutputBody, OutputEvent, Tid};
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::stream::{StreamEntry, StreamId};
use std::collections::BTreeMap;
use std::num::NonZeroU32;

type Fields = BTreeMap<String, Vec<u8>>;

/// The `ev` entry id of an event: `<commit_seq>-<index>`.
pub fn stream_id(id: EventId) -> StreamId {
    StreamId {
        major: id.commit.0,
        minor: u64::from(id.index.get()),
    }
}

/// The event id of an `ev` entry id; `None` when the index is 0 or too large.
pub fn event_id(id: StreamId) -> Option<EventId> {
    let index = u32::try_from(id.minor).ok().and_then(NonZeroU32::new)?;
    Some(EventId {
        commit: CommitSeq(id.major),
        index,
    })
}

/// Id and fields of one `ev` stream entry. The Worker appends it with this
/// explicit id; `tid` and `kind` are flat so entries can be inspected and
/// filtered without parsing `body` (JSON of `OutputBody`).
pub fn encode_ev_entry(event: &OutputEvent) -> Result<(StreamId, Fields), StoreError> {
    let body = serde_json::to_vec(&event.body).map_err(|_| invalid("ev_body_encode"))?;
    let mut fields = Fields::new();
    fields.insert("v".into(), SCHEMA_VERSION.to_string().into_bytes());
    fields.insert("tid".into(), event.tid.0.clone().into_bytes());
    fields.insert("kind".into(), kind_of(&event.body).as_bytes().to_vec());
    fields.insert("body".into(), body);
    Ok((stream_id(event.id), fields))
}

/// The task an `ev` entry belongs to, without decoding its body.
pub fn ev_entry_tid(entry: &StreamEntry) -> Option<Tid> {
    let bytes = entry.fields.get("tid")?;
    String::from_utf8(bytes.clone()).ok().map(Tid)
}

/// Rebuilds the event of an `ev` entry. An unsupported version is
/// `UnsupportedFormat`; any other undecodable entry is `Invalid`. A reader
/// skips such an entry (it still advances its position) and logs it.
pub fn decode_ev_entry(entry: &StreamEntry) -> Result<OutputEvent, StoreError> {
    let text = |name: &str| -> Result<String, StoreError> {
        let bytes = entry.fields.get(name).ok_or_else(|| invalid("ev_field_missing"))?;
        String::from_utf8(bytes.clone()).map_err(|_| invalid("ev_field_utf8"))
    };
    let version = text("v")?.parse::<u32>().map_err(|_| invalid("ev_version"))?;
    supported(version)?;
    let id = event_id(entry.id).ok_or_else(|| invalid("ev_entry_id"))?;
    let body_bytes = entry.fields.get("body").ok_or_else(|| invalid("ev_field_missing"))?;
    let body: OutputBody =
        serde_json::from_slice(body_bytes).map_err(|_| invalid("ev_body_decode"))?;
    Ok(OutputEvent {
        id,
        tid: Tid(text("tid")?),
        body,
    })
}

fn kind_of(body: &OutputBody) -> &'static str {
    match body {
        OutputBody::Status { .. } => "status",
        OutputBody::Artifact { .. } => "artifact",
        OutputBody::Terminal { .. } => "terminal",
    }
}
