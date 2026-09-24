/** Old Web BaseEngineAdapter overrides plus OpenClaw desktop exceptions. */
export function desktopCapabilityPolicy(deployment: string, engine: string) {
  const desktop = deployment === 'local';
  const oc = engine === 'openclaw';
  return {
    resourceWritable: !desktop,
    channels: !desktop,
    nodes: !desktop,
    approval: !desktop,
    markdown: !desktop || oc,
    engineConfig: !desktop || oc,
    screens: !desktop || oc,
    routines: !desktop || oc,
    sessionUploads: !desktop,
  };
}
