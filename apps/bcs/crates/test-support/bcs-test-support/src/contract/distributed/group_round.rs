//! Reusable strict-round assertions; providers supply an isolated empty scope.
use super::ContractResult;
use bcs_dist_model::{Admission, Digest, RequestId, Tid};
use bcs_service_api::port::repo::distributed::group_round::{GroupRoundRepo, GroupStartRequest};

/// Test reference for the bounded-read contract, not a production IO helper.
/// Async consumers must supply their own nonblocking implementation and pass
/// equivalent limit/error cases, including the surrounding event encoding.
pub fn read_round_payload(reader: &mut impl std::io::Read, budget: usize) -> std::io::Result<Vec<u8>> {
    use std::io::Read;
    let limit = u64::try_from(budget).ok().and_then(|v|v.checked_add(1))
        .ok_or_else(||std::io::Error::new(std::io::ErrorKind::InvalidInput,"invalid payload budget"))?;
    let mut bytes=Vec::new();
    reader.take(limit).read_to_end(&mut bytes)?;
    if bytes.len()>budget {
        return Err(std::io::Error::new(std::io::ErrorKind::InvalidData,"round payload too large"));
    }
    Ok(bytes)
}

pub async fn group_round_repo_contract_tests(
    repo: &dyn GroupRoundRepo,
    request: GroupStartRequest,
) -> ContractResult {
    let identity = bcs_service_api::port::repo::distributed::group_round::GroupRequestIdentity::strict(
        &request.input.key.scope, &request.input.key.group, &request.request_id);
    assert_eq!(repo.lookup(&identity).await?, bcs_service_api::port::repo::distributed::group_round::GroupRequestRecord::Absent);
    assert_eq!(repo.get(&request.input.key).await?, None);
    assert_eq!(repo.admit(&request).await?, Admission::New { accepted: request.clone() });
    assert_eq!(repo.lookup(&identity).await?, bcs_service_api::port::repo::distributed::group_round::GroupRequestRecord::Accepted(request.clone()));
    assert_eq!(repo.admit(&request).await?, Admission::Replay { accepted: request.clone() });
    let view = repo.get(&request.input.key).await?.expect("accepted round");
    assert_eq!(view.accepted, request);
    assert!(view.terminal.is_none());
    assert!(view.result.is_none());
    let mut conflict = request.clone();
    conflict.request_digest = Digest("different-normalized-content".into());
    assert_eq!(repo.admit(&conflict).await?, Admission::Conflict);
    let mut competitor = request.clone();
    competitor.request_id = RequestId("competing-request".into());
    competitor.input.key.tid = Tid("competing-task".into());
    assert_eq!(repo.admit(&competitor).await?, Admission::Busy);
    assert_eq!(repo.get(&competitor.input.key).await?, None);
    let mut foreign = request.input.key.clone();
    foreign.scope.principal.0.push_str("-other");
    assert_eq!(repo.get(&foreign).await?, None);
    Ok(())
}
