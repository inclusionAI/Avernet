# Explicit ARCA platform routing context (experiment B)

`ArcaTemplateConfig.arca_tenant_id` is an optional validated string,
propagated unchanged by `PaasServiceFactory` into `ArcaCredentials`. It identifies
the ARCA platform routing tenant associated with the API key, independently of
`tenant_name` (the BaaS business tenant). It is not a cross-tenant switch.

The default is `null`; existing persisted JSON remains readable. Consumers may
use its absence to disable optional NAS recovery while preserving normal create.
The field is excluded from caller `ArcaDeviceConfig.to_create_config()` and
cannot be supplied through per-device configuration. No database migration or
historical identity backfill is performed. Other providers and SPI signatures
are unchanged. The Pydantic template model remains the schema authority.

This is the public propagation portion of an independent design experiment.
It does not prove that storage GET requires this field when a template header
is already present, and it does not implement storage recovery in community
providers. A consuming deployment must verify the API key's routing tenant,
configure the template explicitly and recreate cached credentials/plugins.
Remove the field and refresh those instances to disable that optional behavior.
No default tenant is inferred from business configuration or resource names.
