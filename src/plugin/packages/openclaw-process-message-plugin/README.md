# openclaw-process-message-plugin

Installable OpenClaw plugin package for message processing and persisted-history rewriting.

## Runtime Features

- `process_message`: normalize message content
- `preview_history_message`: preview transcript rewrite output before persistence
- `/process <message>`: run message normalization
- `/process history-status`: inspect active history rewrite settings

## Commands

```bash
npm install
npm run build
npm test
```

## OpenClaw Metadata

- Runtime entrypoint: `package.json -> openclaw.extensions`
- Plugin manifest: `openclaw.plugin.json`
- Plugin module: `src/index.ts` default export `register(api)`
- Persistence hooks: `tool_result_persist` and `before_message_write`
