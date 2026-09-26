# Security Policy

## Supported Versions

Security updates are applied to the latest release only.

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **Do NOT open a public GitHub issue.**
2. Email the maintainers directly with a description of the vulnerability, reproduction steps, and potential impact.
3. You will receive an acknowledgment within 48 hours.
4. A fix or mitigation will be developed and a security advisory published once a patch is available.

## Security Best Practices

When deploying taskguard:

- Store all API keys and secrets in the application config `baas:` section (managed via
  clawweb's `cm_app_config` table row `config_key="baas"`) or in environment variables —
  never hardcode them
- Use `BAAS_API_KEY`, `BAAS_BASE_URL`, and other env vars as deployment fallbacks for external
  service credentials
- Restrict network access to the TaskFlow database (SQLite/MySQL)
- Enable authentication for the HTTP API mode
- Regularly update dependencies to receive security patches
