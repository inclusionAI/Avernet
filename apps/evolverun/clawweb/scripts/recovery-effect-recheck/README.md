# Local session recovery effect recheck

Run from the ClawWeb workspace. Build the shared, Clawevolve and ClawInsight
packages first (`npm run ci:build`). The local process uses the shared policy
implementation from the ClawInsight build. It does not add an online UI or a
batch API.

The operator supplies the existing authenticated ClawWeb connection as
`CW_RECHECK_PRE_URL` / `CW_RECHECK_PROD_URL` and the corresponding
`CW_RECHECK_PRE_HEADERS` / `CW_RECHECK_PROD_HEADERS` JSON object through the
process environment. `CW_RECHECK_ODC_ARGS` is a JSON array of connection options
for the existing, authorized `odc-cli sql` client. Use a read-only database
connection with the same control-plane data as the API. Credentials are never
stored in the progress database or sent to the browser.

```sh
node scripts/recovery-effect-recheck/cli.mjs scan --db /path/to/effect-recheck.sqlite --from YYYY-MM-DD --to YYYY-MM-DD --plane pre --owner OWNER_ID
node scripts/recovery-effect-recheck/cli.mjs run --db /path/to/effect-recheck.sqlite
```

Dates refer to the source database's original creation day; the end date is
exclusive. Set the owner explicitly. The scanner reads the live resume policy
before each page. Scanning does not dispatch any task. `run` starts dispatch and
serves the progress page at [localhost:8732](http://127.0.0.1:8732/); use `view`
with the same database to serve the page without dispatch. The browser requires
no backend credentials and has no operation buttons.

The dispatcher defaults to two simultaneous AIS executions for the database.
Run one dispatcher per database. Stop it to pause new starts; in-flight AIS
executes independently. On restart, it reconciles saved request IDs and existing
AIS steps before taking more work. Each launch re-reads policy and task state.
Uncertain HTTP sends retain their slot and reuse the request ID. The progress
page shows `unknown` as a completed recheck, and reports execution failures and
diagnosis delivery separately. An accepted result has been written to the
diagnosis source. Its appearance and date in DI depend on warehouse sync and
the report's date field.
