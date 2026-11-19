//! The SSE `id:` / `Last-Event-ID` value: an opaque, compact, versioned
//! encoding of the full `TaskCursor`.
//!
//! Format 1 is `1.<base64url("<task id>:<position>")>`. It implies the backend
//! `tbase` and the log generation `1`; the task id contains the session id. A
//! cursor of another backend or generation is not encodable in format 1 and
//! gets no id (the client then falls back to a snapshot on reconnect). Clients
//! never parse the value; any Gateway instance decodes it.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use bcs_dist_model::{BackendId, LogGeneration, ReplayToken, Sid, TaskCursor, Tid};

const FORMAT: &str = "1.";
const BACKEND: &str = "tbase";
/// Changes only if a future rebuild procedure recreates a stream.
const GENERATION: &str = "1";
const MAX_ENCODED_BYTES: usize = 1024;

/// `None` when the cursor cannot be expressed in format 1.
pub fn encode(cursor: &TaskCursor) -> Option<String> {
    let token = &cursor.token;
    if token.backend.0 != BACKEND || token.generation.0 != GENERATION {
        return None;
    }
    if cursor.tid.0.contains(':') || !is_position(&token.value) {
        return None;
    }
    let raw = format!("{}:{}", cursor.tid.0, token.value);
    Some(format!("{FORMAT}{}", URL_SAFE_NO_PAD.encode(raw)))
}

/// `None` for anything that is not a format-1 id: the caller then serves a
/// snapshot first, exactly as without a `Last-Event-ID`.
pub fn decode(id: &str) -> Option<TaskCursor> {
    if id.len() > MAX_ENCODED_BYTES {
        return None;
    }
    let raw = URL_SAFE_NO_PAD.decode(id.strip_prefix(FORMAT)?).ok()?;
    let raw = String::from_utf8(raw).ok()?;
    let (tid, position) = raw.rsplit_once(':')?;
    let (sid, part) = tid.rsplit_once('.')?;
    if sid.is_empty() || part.is_empty() || !is_position(position) {
        return None;
    }
    Some(TaskCursor {
        sid: Sid(sid.to_owned()),
        tid: Tid(tid.to_owned()),
        token: ReplayToken {
            backend: BackendId(BACKEND.to_owned()),
            generation: LogGeneration(GENERATION.to_owned()),
            value: position.to_owned(),
        },
    })
}

/// `<digits>-<digits>`.
fn is_position(value: &str) -> bool {
    let digits = |s: &str| !s.is_empty() && s.len() <= 20 && s.bytes().all(|b| b.is_ascii_digit());
    value.split_once('-').is_some_and(|(a, b)| digits(a) && digits(b))
}
