export type ClawWebMachineEnvironment = "dev" | "pre" | "prod";

export function normalizeMachineEnvironment(value: string | undefined): ClawWebMachineEnvironment {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "pre" || normalized === "prepub") return "pre";
  if (normalized === "prod" || normalized === "gray") return "prod";
  return "dev";
}

export function resolveMachineEnvironment(
  env: NodeJS.ProcessEnv = process.env,
  explicit?: string,
): ClawWebMachineEnvironment {
  return normalizeMachineEnvironment(
    explicit ?? env.SERVER_ENV ?? env.REAL_SERVER_ENV ?? env.ALIPAY_APP_ENV,
  );
}
