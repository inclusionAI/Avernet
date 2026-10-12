use bcs_service_api::{ProviderBasicInfo, ProviderCoreService, ProviderManagementService,
    ProviderRecord, ProviderRepoPort, ServiceError};

fn provider(id: &str, slug: Option<&str>) -> ProviderRecord {
    ProviderRecord {
        provider_id: id.into(), slug: slug.map(str::to_string), name: "Original".into(),
        config: r#"{"downlink":{"auth_mode":"static_bearer","protocol_version":"1.0"}}"#.into(),
        created_by: "alice".into(), owners: r#"["alice"]"#.into(), disabled: false,
        created_at: 1, updated_at: 1,
    }
}

/// Shared persistence contract, run against Memory and each available SQL dialect.
pub async fn provider_repo_port_contract_tests(repo: &dyn ProviderRepoPort) {
    repo.insert_provider(provider("one", Some("first"))).await.unwrap();
    repo.insert_provider(provider("two", Some("second"))).await.unwrap();
    repo.insert_provider(provider("legacy-one", None)).await.unwrap();
    repo.insert_provider(provider("legacy-two", None)).await.unwrap();
    assert!(repo.get_provider_by_slug("missing").await.unwrap().is_none());
    assert_eq!(repo.get_provider_by_slug("first").await.unwrap().unwrap().provider_id, "one");
    assert!(matches!(repo.insert_provider(provider("duplicate", Some("first"))).await,
        Err(ServiceError::Conflict(_))));
    assert!(repo.get_provider("duplicate").await.unwrap().is_none());
    assert_eq!(repo.get_provider("one").await.unwrap().unwrap().slug.as_deref(), Some("first"));

    // Load by ID before mutation, so a stale cached record cannot mask an update.
    let before = repo.get_provider("one").await.unwrap().unwrap();
    assert!(matches!(repo.update_provider_metadata("one", Some("Changed"), Some("{}"),
        Some("second"), 2).await, Err(ServiceError::Conflict(_))));
    assert_eq!(repo.get_provider("one").await.unwrap().unwrap(), before);
    let updated = repo.update_provider_metadata("one", Some("Renamed"), None,
        Some("new-slug"), 3).await.unwrap().unwrap();
    assert_eq!(updated.slug.as_deref(), Some("new-slug"));
    assert!(repo.get_provider_by_slug("first").await.unwrap().is_none());
    assert_eq!(repo.get_provider_by_slug("new-slug").await.unwrap().unwrap().name, "Renamed");
    assert_eq!(repo.update_provider_metadata("one", None, None, None, 4).await.unwrap()
        .unwrap().slug.as_deref(), Some("new-slug"));
    repo.update_provider_metadata("one", None, None, Some("new-slug"), 5).await.unwrap();
    repo.update_provider_disabled("one", true, 6).await.unwrap();
    assert!(repo.get_provider_by_slug("new-slug").await.unwrap().unwrap().disabled);
    assert!(matches!(repo.insert_provider(provider("disabled-duplicate", Some("new-slug"))).await,
        Err(ServiceError::Conflict(_))));
    repo.update_provider_disabled("one", false, 7).await.unwrap();
    assert!(!repo.get_provider_by_slug("new-slug").await.unwrap().unwrap().disabled);
    assert!(repo.update_provider_metadata("missing", None, None, Some("unused"), 8)
        .await.unwrap().is_none());
    assert_eq!(repo.list_providers_by_ids(&["one".into()]).await.unwrap()[0].slug.as_deref(), Some("new-slug"));
    assert!(repo.list_providers().await.unwrap().iter()
        .any(|record| record.slug.as_deref() == Some("new-slug")));
}

/// Discovery portion of the Provider core contract, with a caller-supplied fixture.
pub async fn provider_core_service_contract_tests(
    core: &dyn ProviderCoreService, expected: &ProviderBasicInfo,
) {
    assert_eq!(core.get_provider_by_slug(&expected.slug).await.unwrap().as_ref(), Some(expected));
    assert!(core.get_provider_by_slug("contract-missing").await.unwrap().is_none());
    assert!(matches!(core.get_provider_by_slug("Invalid_slug").await,
        Err(ServiceError::InvalidOperation { .. })));
}

/// Discovery portion of the public application contract, without an auth argument.
pub async fn provider_management_service_contract_tests(
    service: &dyn ProviderManagementService, expected: &ProviderBasicInfo,
) {
    assert_eq!(service.get_provider_by_slug(&expected.slug).await.unwrap().as_ref(), Some(expected));
    assert!(service.get_provider_by_slug("contract-missing").await.unwrap().is_none());
    assert!(matches!(service.get_provider_by_slug("Invalid_slug").await,
        Err(ServiceError::InvalidOperation { .. })));
}
