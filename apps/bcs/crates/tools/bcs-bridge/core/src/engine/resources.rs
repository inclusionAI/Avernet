use std::{collections::HashSet, path::PathBuf};

/// Exclusive physical resource declared by an engine adapter, without building it.
/// Namespace identifies the resource kind, not the Bot or Provider identity.
#[derive(Clone, Debug)]
pub enum EngineResource {
    Path(&'static str, PathBuf),
    Address(&'static str, std::net::SocketAddr),
    Default(&'static str),
}

/// Check canonical path/address claims from adapters before acquiring resources.
pub fn validate_resource_claims(resources: impl IntoIterator<Item = EngineResource>) -> anyhow::Result<()> {
    let mut claims = HashSet::new();
    #[cfg(unix)]
    let mut files = HashSet::new();
    for claim in resources {
        let (namespace, value) = match claim {
            EngineResource::Path(namespace, path) => {
                #[cfg(unix)]
                if let Ok(metadata) = std::fs::metadata(&path) {
                    use std::os::unix::fs::MetadataExt;
                    if !files.insert((namespace, metadata.dev(), metadata.ino())) {
                        anyhow::bail!("Multiple Bots alias the same desktop resource");
                    }
                }
                (namespace, crate::bridge_config::resolved_path(&path)?.into_os_string())
            },
            EngineResource::Address(namespace, address) => (namespace, address.to_string().into()),
            EngineResource::Default(namespace) => (namespace, std::ffi::OsString::new()),
        };
        if !claims.insert((namespace, value)) { anyhow::bail!("Multiple Bots claim the same desktop resource"); }
    }
    Ok(())
}
