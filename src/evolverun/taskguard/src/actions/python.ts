import { runOpenClawCommand, type CommandRunner } from "../command-runner.js";

export type PythonRunner = {
  runPython: (
    script: string,
    args: string[],
    env?: Record<string, string>,
  ) => Promise<Record<string, unknown>>;
};

export function createPythonRunner(runCommand: CommandRunner = runOpenClawCommand): PythonRunner {
  return {
    async runPython(script, args, env) {
      const options: Parameters<CommandRunner>[0] = {
        argv: ["python3", script, ...args],
        timeoutMs: 120_000,
      };
      if (env) options.env = { ...process.env, ...env };
      const result = await runCommand(options);
      const stderrText = result.stderr.trim();
      if (result.code !== 0) {
        throw new Error(stderrText || `Python script exited with code ${result.code}`);
      }
      const output = result.stdout.trim();
      if (!output) {
        throw new Error(`Python script returned empty stdout${stderrText ? `; stderr: ${stderrText.substring(0, 500)}` : ""}`);
      }
      try {
        return JSON.parse(output);
      } catch {
        throw new Error(`Python script returned non-JSON stdout: ${output.substring(0, 500)}${stderrText ? `; stderr: ${stderrText.substring(0, 500)}` : ""}`);
      }
    },
  };
}
