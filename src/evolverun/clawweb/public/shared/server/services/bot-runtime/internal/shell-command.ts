function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

const REPAIR_RUNTIME_USER = "admin";
const REPAIR_RUNTIME_HOME = "/home/admin";
const REPAIR_RUNTIME_USER_EXIT_CODE = 78;

/**
 * Remote command transports inherit the container's default user. Legacy ARCA
 * sandboxes may therefore start commands as root even though OpenClaw runs as
 * admin. Drop that inherited privilege before every Repair read or approved
 * container action, and fail closed for any other execution identity.
 */
export function buildRuntimeUserCommand(command: string): string {
  const payload = [
    `export HOME=${REPAIR_RUNTIME_HOME} USER=${REPAIR_RUNTIME_USER} LOGNAME=${REPAIR_RUNTIME_USER}`,
    `cd ${REPAIR_RUNTIME_HOME} || exit ${REPAIR_RUNTIME_USER_EXIT_CODE}`,
    "umask 077",
    `exec bash --noprofile --norc -c ${shellQuote(command)}`,
  ].join("; ");
  const identityError = "Repair target command must run as admin";
  return [
    `repair_uid=$(id -u) || exit ${REPAIR_RUNTIME_USER_EXIT_CODE}`,
    'if [ "$repair_uid" = "0" ]; then',
    `  id ${REPAIR_RUNTIME_USER} >/dev/null 2>&1 || { printf '%s\\n' ${shellQuote(identityError)} >&2; exit ${REPAIR_RUNTIME_USER_EXIT_CODE}; }`,
    `  command -v su >/dev/null 2>&1 || { printf '%s\\n' ${shellQuote(identityError)} >&2; exit ${REPAIR_RUNTIME_USER_EXIT_CODE}; }`,
    `  exec su ${REPAIR_RUNTIME_USER} -c ${shellQuote(payload)}`,
    "fi",
    `test "$(id -un)" = ${REPAIR_RUNTIME_USER} || { printf '%s\\n' ${shellQuote(identityError)} >&2; exit ${REPAIR_RUNTIME_USER_EXIT_CODE}; }`,
    `exec bash --noprofile --norc -c ${shellQuote(payload)}`,
  ].join("\n");
}
