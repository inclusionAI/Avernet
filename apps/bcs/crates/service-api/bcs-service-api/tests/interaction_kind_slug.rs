use bcs_service_api::InteractionKind;

#[test]
fn exec_maps_to_exec_slug() {
    assert_eq!(InteractionKind::Exec.as_slug(), "exec");
}

#[test]
fn ask_user_maps_to_ask_user_slug() {
    assert_eq!(InteractionKind::AskUser.as_slug(), "ask_user");
}

#[test]
fn mode_switch_maps_to_mode_switch_slug() {
    assert_eq!(InteractionKind::ModeSwitch.as_slug(), "mode_switch");
}
