use bcs_service_api::DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS;

#[test]
fn default_provider_callback_timeout_is_three_hours() {
    assert_eq!(DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS, 10_800_000);
}
