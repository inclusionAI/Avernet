use std::sync::Arc;

use bcs_service_api::{
    GroupManagementService, GroupParticipantModeCommand, GroupPatchSettingsCommand,
    GroupRemoveMemberCommand, GroupRoutingPolicyCommand, GroupStatusCommand,
    GroupTerminateCommand, GroupUpdateLabelCommand, GroupUpdateVisibilityCommand,
    GroupUpdateWorkspaceCommand, ParticipantMode, Workspace,
};
use bcs_group::GroupManagementWithRuntimeCleanup;
use bcs_test_support::{NoopCollaborationRuntimeService, NoopGroupManagementService};

#[tokio::test]
async fn cleanup_wrapper_forwards_non_delete_operations() {
    let service = GroupManagementWithRuntimeCleanup::new(
        Arc::new(NoopGroupManagementService),
        Arc::new(NoopCollaborationRuntimeService),
    );

    assert!(
        service
            .update_status(GroupStatusCommand {
                caller_actor_id: Some("driver".to_string()),
                group_id: "group-1".to_string(),
                status: "active".to_string(),
            })
            .await
            .is_err()
    );
    assert!(
        service
            .remove_member(GroupRemoveMemberCommand {
                caller_actor_id: Some("driver".to_string()),
                group_id: "group-1".to_string(),
                bot_id: "worker".to_string(),
            })
            .await
            .is_err()
    );
    assert!(
        service
            .terminate_group(GroupTerminateCommand {
                caller_actor_id: "driver".to_string(),
                group_id: "group-1".to_string(),
            })
            .await
            .is_err()
    );
    assert!(
        service
            .update_label(GroupUpdateLabelCommand {
                caller_actor_id: "driver".to_string(),
                group_id: "group-1".to_string(),
                label: Some("Renamed".to_string()),
            })
            .await
            .is_err()
    );
    assert!(
        service
            .update_visibility(GroupUpdateVisibilityCommand {
                caller_actor_id: "driver".to_string(),
                group_id: "group-1".to_string(),
                visibility: "private".to_string(),
            })
            .await
            .is_err()
    );
    assert!(
        service
            .update_workspace(GroupUpdateWorkspaceCommand {
                caller_actor_id: Some("driver".to_string()),
                group_id: "group-1".to_string(),
                workspace: Workspace::default(),
            })
            .await
            .is_err()
    );
    assert!(
        service
            .update_routing_policy(GroupRoutingPolicyCommand {
                caller_actor_id: Some("driver".to_string()),
                group_id: "group-1".to_string(),
                mode: None,
                default_bot_final_delivery: None,
                sender_routes: None,
            })
            .await
            .is_err()
    );
    assert!(
        service
            .update_participant_mode(GroupParticipantModeCommand {
                caller_actor_id: "driver".to_string(),
                group_id: "group-1".to_string(),
                actor_id: "worker".to_string(),
                mode: ParticipantMode::Muted,
            })
            .await
            .is_err()
    );
    assert!(
        service
            .patch_group_settings(GroupPatchSettingsCommand {
                group_id: "group-1".to_string(),
                service_spec: None,
            })
            .await
            .is_err()
    );
}
