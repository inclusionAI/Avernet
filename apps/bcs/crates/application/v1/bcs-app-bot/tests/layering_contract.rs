use std::fs;
use std::path::{Path, PathBuf};

fn production_sources(directory: &Path, sources: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(directory).expect("read bcs-app-bot production source directory") {
        let path = entry.expect("read production source entry").path();
        if path.is_dir() {
            production_sources(&path, sources);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            sources.push(path);
        }
    }
}

#[test]
fn production_bot_application_depends_on_core_services_not_repository_ports() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut sources = Vec::new();
    production_sources(&manifest_dir.join("src"), &mut sources);
    assert!(
        !sources.is_empty(),
        "bcs-app-bot production source walk found no files",
    );

    for source_path in sources {
        let source = fs::read_to_string(&source_path)
            .expect("read bcs-app-bot production source");
        for forbidden in [
            "BotControlPlaneRepoPort",
            "ProviderRepoPort",
            "ProviderBotBindingRepoPort",
        ] {
            assert!(
                !source.contains(forbidden),
                "bcs-app-bot production code ({}) must depend on Core services, not {forbidden}",
                source_path.display(),
            );
        }
    }
}
