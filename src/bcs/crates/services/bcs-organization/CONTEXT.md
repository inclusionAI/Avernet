# bcs-organization Context

## Provides

- OrganizationCore implementation.
- OrganizationManagement application service.

## Consumes

- OrganizationRepoPort, ProviderRepoPort, ProviderBotBindingRepoPort, BotRegistryCoreService, and ProviderCoreService contracts.

## Runtime ownership

This crate owns Organization lifecycle, membership, candidate selection, and Provider-grant policy. It does not own HTTP, SQL, Bot mutation, friendship, Group, WebSocket, or delivery behavior.
