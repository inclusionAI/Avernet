use std::fs;
use std::path::Path;

#[test]
fn production_bot_application_depends_on_core_services_not_repository_ports() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let source = fs::read_to_string(manifest_dir.join("src/lib.rs"))
        .expect("read bcs-app-bot production source");

    for forbidden in [
        "BotControlPlaneRepoPort",
        "ProviderRepoPort",
        "ProviderBotBindingRepoPort",
    ] {
        assert!(
            !source.contains(forbidden),
            "bcs-app-bot production code must depend on Core services, not {forbidden}",
        );
    }
}
