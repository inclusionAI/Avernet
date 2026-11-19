use super::*;
use bcs_domain::Session;
use bcs_service_api::application::caller_identity::HumanCallerAuthSource;

/// Only trusted channel submissions may use the single-speaker exception.
/// Workbench and other ingress keep their existing lease checks.
pub(super) async fn is_exclusive_channel_submission(
    flow: &BcsMessageFlow,
    command: &WebSendCommand,
    group: &Group,
    session: Option<&Session>,
) -> ServiceResult<bool> {
    if command.source_im_message_id.as_deref().is_none_or(str::is_empty)
        || !command.caller_ingress.as_ref().is_some_and(|ingress| {
            ingress.auth_source() == HumanCallerAuthSource::ChannelIngress
        })
    {
        return Ok(false);
    }
    let (Some(channel), Some(session)) = (flow.channel.get(), session) else {
        return Ok(false);
    };
    match channel.exclusive_session_speaker(session, group).await {
        Ok(Some(speaker)) if speaker == command.from_actor_id => Ok(true),
        Ok(Some(_)) => Err(ServiceError::Forbidden(
            "channel sender is not the speaker bound to this Session".into(),
        )),
        Ok(None) => Ok(false),
        Err(error) => {
            warn!(%error, "could not verify exclusive channel Session; requiring speaking lease");
            Ok(false)
        }
    }
}
