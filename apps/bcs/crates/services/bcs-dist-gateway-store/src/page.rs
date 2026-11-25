//! Page tokens of the per-session task index: the last entry of the previous
//! page, as `1.<base64url("<score ms>:<task id>")>`.

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use bcs_dist_model::PageToken;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::zset::ZSetEntry;

const FORMAT: &str = "1.";

pub(crate) fn encode(last: &ZSetEntry) -> PageToken {
    let raw = format!("{}:{}", last.score as i64, last.member);
    PageToken(format!("{FORMAT}{}", URL_SAFE_NO_PAD.encode(raw)))
}

pub(crate) fn decode(token: &PageToken) -> Result<ZSetEntry, StoreError> {
    let invalid = || StoreError::Invalid {
        code: "page_token".to_owned(),
    };
    let body = token.0.strip_prefix(FORMAT).ok_or_else(invalid)?;
    let raw = URL_SAFE_NO_PAD.decode(body).map_err(|_| invalid())?;
    let raw = String::from_utf8(raw).map_err(|_| invalid())?;
    let (score, member) = raw.split_once(':').ok_or_else(invalid)?;
    let score = score.parse::<i64>().map_err(|_| invalid())?;
    if member.is_empty() {
        return Err(invalid());
    }
    Ok(ZSetEntry {
        member: member.to_owned(),
        score: score as f64,
    })
}
