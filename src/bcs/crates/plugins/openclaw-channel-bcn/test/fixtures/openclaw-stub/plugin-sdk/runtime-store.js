export function createPluginRuntimeStore(errorMessage) {
  let _runtime;
  return {
    setRuntime(rt) { _runtime = rt; },
    getRuntime() { if (!_runtime) throw new Error(errorMessage); return _runtime; },
  };
}
