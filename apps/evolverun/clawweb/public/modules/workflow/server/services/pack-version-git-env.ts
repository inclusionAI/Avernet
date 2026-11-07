/** Preserve operator-provided transport settings, never Git config or object-store overrides. */
export function packVersionGitEnvironment(): NodeJS.ProcessEnv {
  const transport = new Set(['GIT_ALLOW_PROTOCOL', 'GIT_SSH', 'GIT_SSH_COMMAND', 'GIT_SSH_VARIANT', 'GIT_SSL_CAINFO', 'GIT_SSL_CAPATH',
    'GIT_SSL_CERT', 'GIT_SSL_KEY', 'GIT_SSL_CERT_PASSWORD_PROTECTED']);
  return {
    ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_') || transport.has(key))),
    GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_TERMINAL_PROMPT: '0', GIT_NO_LAZY_FETCH: '1',
    GIT_NO_REPLACE_OBJECTS: '1',
  };
}
