//! Standalone policy loading: no overlays or environment substitution.
use std::{path::Path, sync::Arc, collections::BTreeMap};
use serde::Deserialize;
use bcs_service_api::types::catalog_labels::{CatalogLabelPolicy, CatalogLabelRule};

pub(super) fn load(dir:&Path)->Result<Arc<CatalogLabelPolicy>,String> {
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct PolicyFile { labels:BTreeMap<String,CatalogLabelRule> }
    let path=dir.join("catalog-labels.toml");
    let source=std::fs::read_to_string(&path).map_err(|e|format!("{}: {e}",path.display()))?;
    let file:PolicyFile=toml::from_str(&source).map_err(|e|format!("{}: {e}",path.display()))?;
    CatalogLabelPolicy::new(file.labels).map(Arc::new).map_err(|e|format!("{}: {e}",path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn required_file_strict_dictionary_and_no_interpolation() {
        let dir=tempfile::tempdir().unwrap();
        assert!(load(dir.path()).is_err());
        for source in ["bad", "[labels.stage]\nallowed_values=['a']", "[labels.visibility]\nallowed_values=['a']\nallow_multiple=false", "[labels.stage]\nallowed_values=[]\nallow_multiple=true"] {
            std::fs::write(dir.path().join("catalog-labels.toml"),source).unwrap();
            assert!(load(dir.path()).is_err(),"{source}");
        }
        std::fs::write(dir.path().join("catalog-labels.toml"),"[labels]").unwrap();
        assert!(load(dir.path()).is_ok());
        std::fs::write(dir.path().join("catalog-labels.toml"),"[labels.stage]\nallowed_values=['${UNEXPANDED}']\nallow_multiple=true").unwrap();
        let policy=load(dir.path()).unwrap();
        assert!(policy.normalize(serde_json::from_str("{\"stage\":[\"${UNEXPANDED}\"]}").unwrap()).is_ok());
    }
}

#[cfg(test)]
mod loading_contract {
    use super::*;
    #[test]
    fn single_file_requires_policy_and_rejects_inline_overlay_override() {
        let dir=tempfile::tempdir().unwrap();let path=dir.path().join("bcs-config.toml");
        std::fs::write(&path,"bots_base_dir='/bots'\n").unwrap();
        assert!(crate::BcsConfig::from_file(&path).unwrap_err().to_string().contains("catalog-labels.toml"));
        std::fs::write(dir.path().join("catalog-labels.toml"),"[labels]\n").unwrap();
        assert!(crate::BcsConfig::from_file(&path).is_ok());
        std::fs::write(&path,"bots_base_dir='/bots'\n[catalog_labels]\n").unwrap();
        assert!(crate::BcsConfig::from_file(&path).is_err());
        // Environment-suffixed policy files cannot extend/replace the selected dictionary.
        for env in ["local","dev","pre","prod","gray"] {
            std::fs::write(dir.path().join(format!("catalog-labels-{env}.toml")),"[labels.stage]\nallowed_values=['unexpected']\nallow_multiple=true").unwrap();
            assert!(load(dir.path()).unwrap().normalize(serde_json::from_str("{\"stage\":[\"unexpected\"]}").unwrap()).is_err());
        }
    }
}
