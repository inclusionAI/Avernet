/** Mock the server's active-history response; it is not an origin proof. */
export function historyPackFixture(activeToken: string, yaml: string) {
  const b64 = (text: string) => Buffer.from(text).toString('base64')
  return { workflowId: 'flow', packId: 'pack', baseRef: 'refs/tags/deploy/flow/#1', baseCommit: 'a'.repeat(40), draftHead: null,
    activeHistory: { protocol: 'active-history-v1', activeToken }, versionFiles: [
      { path: 'workflow.pack.yaml', mode: '100644', contentBase64: b64('id: pack\nworkflows: [flow]\n') },
      { path: 'workflows/flow.yaml', mode: '100644', contentBase64: b64(yaml) },
    ] }
}
