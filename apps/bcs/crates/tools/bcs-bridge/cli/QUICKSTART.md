# BCS bridge preview for macOS

`bcs-bridge` connects a local agent engine to BCS. This release contains only
the bridge, not `bcs-cli`, a BCS server, or the QwenWork desktop application.
Apple Silicon and Intel macOS have separate prebuilt archives. Rust is not
required on the user's machine. This is a preview distribution.

## Install a specific version

Download `bcs-bridge-installer.sh` from the chosen `bcs-bridge-v<VERSION>`
GitHub Release at https://github.com/inclusionAI/Avernet/releases.
Use that version's installer for upgrades or rollbacks; do not use the
repository-wide `releases/latest` endpoint, which can select another product.

```sh
sh bcs-bridge-installer.sh
~/.local/bin/bcs-bridge --version
~/.local/bin/bcs-bridge --help
```

The installer selects the archive for your CPU and installs into `~/.local/bin`
without sudo. Follow its PATH instructions. Alternatively download the CPU's
`.tar.gz` and `.sha256`, verify with `shasum -a 256 -c <archive>.sha256`, extract
the archive and copy only `bcs-bridge` into a directory on your PATH.

## Connect QwenWork

1. Install and open a compatible QwenWork desktop version. The adapter has
   been inspected on QwenWorkCN 1.2.5; future desktop protocols require testing.
2. Obtain a registration token from your Avernet deployment. Set the deployment
   API URL explicitly; there is no private service endpoint bundled into this
   release. Inspect the registration options:

   ```sh
   bcs-bridge register --help
   ```

   Register with `--engine qwenwork --mode plugin`, your `--token`, `--api-url`,
   `--bot-name` and `--cwd`. Registration does not complete all QwenWork setup.
3. Initialize the registered Bot's local settings and Hooks:

   ```sh
   bcs-bridge --config "$HOME/.bcn-bridge/qwenwork.toml" init --engine qwenwork
   ```

   Use the same `--config` path you chose during registration. Initialization
   detects the desktop database, generates separate channel/Hook credentials,
   backs up configuration and preserves unrelated settings. Enter the printed
   WebSocket URL, Bot ID and Secret in QwenWork's WeCom Bot channel. Reload
   desktop Hooks. If detection is ambiguous, pass `--option database_path=/path/agents.db`
   and `--option settings_path=/path/settings.json`. Changing `--option listen=...`
   removes only Hooks matching the previous URL and credential.
   Provider provisioning and registration authorization belong to your Avernet
   deployment; the [minimal Singlebox guide](https://github.com/inclusionAI/Avernet/blob/dev/singlebox/QWENWORK.md)
   describes local validation. Bridge does not create or authorize Providers.
4. Start and check the bridge:

   ```sh
   bcs-bridge --config "$HOME/.bcn-bridge/qwenwork.toml" start --daemon
   bcs-bridge status
   curl http://127.0.0.1:52491/health
   ```

   The health URL uses the example engine port; substitute your configured
   port. It reports local QwenWork connectivity, not BCS routing health.
   Confirm BCS connectivity in your Avernet deployment and send a short task.
5. Stop gracefully with `bcs-bridge stop` before replacing the binary. Start
   again after installing the selected release. Configuration stays local.

Use `--config /absolute/path/to/bridge.toml` to choose a non-default config.
The installer does not change QwenWork settings, register a Bot or start a
background service. Before uninstalling, disable the QwenWork WeCom channel, stop the bridge, then
remove its owned global Hooks while you still have the binary and configuration:

```sh
bcs-bridge stop
bcs-bridge --config "$HOME/.bcn-bridge/qwenwork.toml" deinit --engine qwenwork
```

`deinit` requires beta.3 or newer and preserves unrelated Hooks and Bot identity.
Reload/restart desktop Hooks after cleanup, then remove the binary if desired.
For beta.2, remove only HTTP Hook entries matching the saved listener URL and
`hook_secret` from the configured desktop settings JSON; keep other Hooks and
back up that file privately first. If a custom settings path was used before
beta.3, pass the same `--option settings_path=/absolute/path/settings.json`.
Do not restore an old whole settings backup over newer unrelated settings.
Keep or remove local bridge configuration separately as needed.

## Preview limitations

Text, tools and task lifecycle have local validation; this is not a guarantee
of complete desktop-version compatibility. Current QwenWork IM system prompts
discourage AskUserQuestion, even where optional bridge question control is
configured. Subagent tool/interaction forwarding remains incomplete. Execute
permission approvals remain in QwenWork. This macOS-only release does not claim
Windows named-pipe question support or Linux desktop compatibility.
