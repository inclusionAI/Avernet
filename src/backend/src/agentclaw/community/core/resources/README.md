# `agentclaw.community.core.resources`

Resource domain — file upload, listing, deletion against the device filesystem (with NAS path resolution).

## Context Boundary

```yaml
purpose: "Resource domain — file upload, listing, deletion against the device filesystem (with NAS path resolution)."
provides:
  - "ResourceService"
  - "FileService"
  - "Resource repository"
consumes:
  - "BotRepository"
  - "BotService"
  - "DeviceService"
  - "DeviceFileSystem (transitional: FileService P0 else-branch injection)"
  - "PassportPlugin (yuque permission sync, injected via the plugin Protocol)"
internal_dependencies:
  - agentclaw.community.core.repository.protocols.bot    # repository contracts consumed by this module
  - agentclaw.community.core.repository.protocols.platform    # repository contracts consumed by this module
  - agentclaw.community.core.bot_management
  - agentclaw.community.core.devices
  - agentclaw.community.core.workspace
  - agentclaw.community.log
  - agentclaw.community.core.devices.services.device_filesystem
  - agentclaw.community.plugin_api.passport
```

### Change impact

Upload signatures here flow up to api/resources routes — the kw-only (data, filename) shape is the R7-correct contract; reverts to UploadFile pull FastAPI back into core.
