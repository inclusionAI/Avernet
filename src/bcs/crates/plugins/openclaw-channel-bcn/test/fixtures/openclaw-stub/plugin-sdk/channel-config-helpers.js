export function createScopedChannelConfigBase(opts) {
  return opts;
}

export function createScopedAccountConfigAccessors(params) {
  const base = {
    resolveAllowFrom({ cfg, accountId }) {
      const account = params.resolveAccount({ cfg, accountId });
      const allowFrom = params.resolveAllowFrom(account);
      return (allowFrom ?? []).map((entry) => String(entry));
    },
    formatAllowFrom({ allowFrom }) {
      return params.formatAllowFrom(allowFrom);
    },
  };
  if (!params.resolveDefaultTo) return base;
  return {
    ...base,
    resolveDefaultTo({ cfg, accountId }) {
      const account = params.resolveAccount({ cfg, accountId });
      const raw = params.resolveDefaultTo(account);
      return raw != null ? String(raw).trim() || undefined : undefined;
    },
  };
}

export function createScopedDmSecurityResolver(opts) {
  return (account) => ({
    policy: opts.resolvePolicy?.(account) ?? opts.defaultPolicy ?? 'open',
    allowFrom: opts.resolveAllowFrom?.(account) ?? [],
  });
}
