export function formatAllowFromLowercase(opts) {
  return (opts.allowFrom ?? []).map(s => typeof s === 'string' ? s.toLowerCase() : s);
}
