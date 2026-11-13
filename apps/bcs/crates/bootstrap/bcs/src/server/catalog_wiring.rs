//! Selection only: catalog behavior and lifecycle remain owned by bcs-fusion.
use std::sync::Arc;
use bcs_fusion::CardChangeQueue;

fn enabled(config: &crate::BcsConfig) -> bool {
    config.bcsfuse.enabled && config.bcsfuse.api_version == bcs_fuse_client::BcsFuseApiVersion::V2
}

pub(super) fn new_card_changes(config: &crate::BcsConfig) -> Option<Arc<CardChangeQueue>> {
    enabled(config).then(|| Arc::new(CardChangeQueue::default()))
}

pub(super) fn select_card_changes(
    config: &crate::BcsConfig, supplied: Option<Arc<CardChangeQueue>>,
) -> crate::Result<Option<Arc<CardChangeQueue>>> {
    if !enabled(config) {
        if supplied.is_some() {
            return Err(crate::BcsError::InvalidConfig(
                "shared Card change queue requires enabled Fuse V2 integration".into(),
            ));
        }
        return Ok(None);
    }
    Ok(Some(supplied.unwrap_or_else(|| Arc::new(CardChangeQueue::default()))))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shared_queue_is_preserved_and_disabled_modes_cannot_orphan_it() {
        let queue = Arc::new(CardChangeQueue::default());
        let mut config = crate::BcsConfig::default();
        for enabled in [false, true] {
            for version in [bcs_fuse_client::BcsFuseApiVersion::V1, bcs_fuse_client::BcsFuseApiVersion::V2] {
                config.bcsfuse.enabled = enabled;
                config.bcsfuse.api_version = version;
                let supplied = select_card_changes(&config, Some(queue.clone()));
                let default = select_card_changes(&config, None).unwrap();
                if enabled && version == bcs_fuse_client::BcsFuseApiVersion::V2 {
                    assert!(Arc::ptr_eq(&supplied.unwrap().unwrap(), &queue));
                    assert!(default.is_some());
                } else {
                    assert!(supplied.is_err());
                    assert!(default.is_none());
                }
            }
        }
    }

    #[test]
    fn new_card_changes_materializes_only_for_enabled_v2() {
        let mut config = crate::BcsConfig::default();
        config.bcsfuse.enabled = false;
        config.bcsfuse.api_version = bcs_fuse_client::BcsFuseApiVersion::V2;
        assert!(new_card_changes(&config).is_none());
        config.bcsfuse.enabled = true;
        config.bcsfuse.api_version = bcs_fuse_client::BcsFuseApiVersion::V1;
        assert!(new_card_changes(&config).is_none());
        config.bcsfuse.api_version = bcs_fuse_client::BcsFuseApiVersion::V2;
        assert!(new_card_changes(&config).is_some());
    }
}
