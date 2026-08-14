use bcs_service_api::DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS;

#[test]
fn default_provider_callback_timeout_is_one_hour() {
    assert_eq!(DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS, 3_600_000);
}
