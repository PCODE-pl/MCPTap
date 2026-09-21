# What MCPTap does

MCPTap can:

* Route traffic to OpenRouter or Requesty
* Force all requests to use a configured model
* Use a different model for plan mode
* Pin or restrict OpenRouter provider routing
* Intercept selected MCP tool calls
* Execute real MCP tools locally through stdio
* Log upstream requests and responses
* Run a configurable hook before client tool calls to allow or block them
* Rewrite tool call arguments via the hook (e.g. wrap shell commands with [RTK](https://github.com/rtk-ai/rtk) for token compression)
* Serve simple UI interface for debugging requests
* Expose a local health endpoint

## High-level flow

```text
AI client
  │
  │ OpenAI-compatible request
  ▼
MCPTap
  │  rewrites payload: forced model, plan-mode switching,
  │  provider pinning, per-model instructions, tool injection
  │
  │  rewritten / routed request
  ▼
OpenRouter or Requesty
  │
  │  model response
  ▼
MCPTap
  │
  ├─ model calls an intercepted MCP tool:
  │      MCPTap calls the MCP server locally, feeds the tool
  │      result back to the model, and loops to the upstream again
  │
  ├─ model calls a client tool and the tool-call hook is enabled:
  │      MCPTap runs the hook script —
  │        allow: returns the saved model response to the client
  │        block: feeds the block message back to the model,
  │               then passes through the next response once
  │
  └─ final response (no intercepted or client tool calls pending)
       ▼
     AI client
```

## Main use cases

MCPTap is designed for workflows like:

* using Codex/Hermes Agent CLI through OpenRouter or Requesty,
* forcing a cheaper model for normal work and a stronger model for planning,
* giving a weaker model access to a stronger “expert” model through an MCP tool,
* disabling access to sensitive files,
* hiding complex MCP orchestration from the client,
* debugging model/tool traffic,
* testing provider fallback behavior,
* controlling OpenRouter provider selection.

## Installation

Install the latest release:

```sh
curl -fsSL https://github.com/PCODE-pl/MCPTap/releases/latest/download/setup.sh | sh
```

If `curl` is not available:

```sh
wget -qO- https://github.com/PCODE-pl/MCPTap/releases/latest/download/setup.sh | sh
```

The installer creates a local Python virtual environment, installs MCPTap files, copies example configuration files, and tries to install a user service.

Default paths:

```text
~/.local/share/mcptap       application files and logs database
~/.local/bin/mcptap         executable wrapper
~/.config/mcptap            configuration files
```

### File access blocking (optional, Linux only)

To build and install the `LD_PRELOAD` file-block library during installation, pass `--with-file-block`:

```sh
sh setup.sh --with-file-block
```

Or when piping from `curl`/`wget`:

```sh
curl -fsSL https://github.com/PCODE-pl/MCPTap/releases/latest/download/setup.sh | sh -s -- --with-file-block
```

This option is **Linux-only**. It requires a C compiler (`gcc` or `cc`), `make`, and C library headers (`libc-dev`/`glibc-devel`). The installer checks for these tools and reports installation instructions if any are missing.

When `--with-file-block` is used on a **new** installation (where `proxy.env` does not already exist), the installer:

1. Builds `libmcptap_fileblock.so` from the `file_block/` source directory.
2. Installs it to `~/.local/lib/libmcptap_fileblock.so`.

On subsequent runs with `--with-file-block`, the library is rebuilt and reinstalled, but `proxy.env` is left untouched (to preserve user edits). Use `--force-config` to reset `proxy.env` to defaults and re-wire the library path.

On macOS, `--with-file-block` is silently skipped (the file-block library is not yet supported on macOS).

After installation, start Codex with the library loaded:

```sh
LD_PRELOAD=~/.local/lib/libmcptap_fileblock.so codex
```

Hermes Agent can be started in the same way:

```sh
LD_PRELOAD=~/.local/lib/libmcptap_fileblock.so hermes
```

See the [Tool-call hook](docs/FEATURES.md#7-tool-call-hook) section for details on how `blocked_files` from the hook are enforced by this library.

## Configuration files

After installation, edit the files in:

```sh
~/.config/mcptap/
```

Important files:

```text
proxy.env          main MCPTap configuration
openrouter.env     OpenRouter model and API key configuration
requesty.env       Requesty model and API key configuration
meta.env           Meta model and API key configuration
nano-gpt.env       NanoGPT model and API key configuration
llmtr.env          LLMTR model and API key configuration
[...].env          All other providers model and API key configuration
mcp-intercept.yaml optional MCP tool interception configuration
per-model.yaml     optional per-model instruction overrides
use_tool_hook.py   optional tool-call hook script (runs before client tool calls)
```

## Quick start

### 1. Select the upstream provider

Edit:

```sh
~/.config/mcptap/proxy.env
```

Example for OpenRouter:

```env
MCPTAP_UPSTREAM_PROVIDER=openrouter
MCPTAP_LISTEN_HOST=127.0.0.1
MCPTAP_LISTEN_PORT=8787
```

Example for Requesty:

```env
MCPTAP_UPSTREAM_PROVIDER=requesty
MCPTAP_LISTEN_HOST=127.0.0.1
MCPTAP_LISTEN_PORT=8787
```

Supported upstream providers:

```text
openrouter
requesty
meta
nano-gpt
llmtr
kenari
unorouter
runinfra
nvidia
opencode
orcarouter
aihubmix
tokenrouter
inferx
kilo
pendra
vercel
zenmux
abliteration-ai
above
agentrouter
agnes
ai-router
aiand
ainetcafe
aixy
aki-io
alibaba
302ai
abacus
alibaba-cn
alibaba-coding-plan
alibaba-coding-plan-cn
alibaba-token-plan
alibaba-token-plan-cn
amazon-bedrock
ambient
amd
anthropic
anyapi
arcee
atomic-chat
auriko
azure
azure-cognitive-services
bailing
baseten
berget
blueclaw
bothub
cerebras
chutes
clarifai
claudinio
cline-pass
cloudferro-sherlock
cloudflare-ai-gateway
cloudflare-workers-ai
cohere
coralbricks
cortecs
crof
crossmodel
crusoe
daoxe
databricks
deepinfra
deepseek
digitalocean
dinference
drun
ebcloud
echo
edenai
```

### 2. Configure the provider

For OpenRouter, edit:

```sh
~/.config/mcptap/openrouter.env
```

Example:

```env
MCPTAP_API_KEY=sk-or-v1-...
MCPTAP_MODEL=deepseek/deepseek-v4-flash:floor
MCPTAP_PLAN_MODE_MODEL=z-ai/glm-5.2:floor
```

For Requesty, edit:

```sh
~/.config/mcptap/requesty.env
```

Example:

```env
MCPTAP_API_KEY=rqsty-sk-...
MCPTAP_MODEL=nvidia/nemotron-3-nano-30b-a3b:free
MCPTAP_PLAN_MODE_MODEL=zai/glm-5.2:floor
```

For Meta, edit:

```sh
~/.config/mcptap/meta.env
```

For NanoGPT, edit:

```sh
~/.config/mcptap/nano-gpt.env
```

Example:

```env
MCPTAP_API_KEY=sk-nano-...
MCPTAP_MODEL=openai/gpt-5.6-sol
MCPTAP_PLAN_MODE_MODEL=openai/gpt-5.6-sol
```

For LLMTR, edit:

```sh
~/.config/mcptap/llmtr.env
```

Example:

```env
MCPTAP_API_KEY=llmtr-...
MCPTAP_MODEL=zai/glm-5.2
MCPTAP_PLAN_MODE_MODEL=zai/glm-5.2
MCPTAP_USE_CHAT_COMPLETIONS=true
```

## Codex configuration

Example Codex configuration:

```toml
model_provider = "mcptap"
model = "openai/gpt-5.5"
model_context_window = 1000000

# This value must be different from MCPTAP_PLAN_MODE_TRIGGER.
# For this reasoning effort, MCPTap will use MCPTAP_MODEL
# from the selected provider env file.
model_reasoning_effort = "xhigh"

# This value must match MCPTAP_PLAN_MODE_TRIGGER.
# For this reasoning effort, MCPTap will use MCPTAP_PLAN_MODE_MODEL
# from the selected provider env file.
plan_mode_reasoning_effort = "max"

model_supports_reasoning_summaries = false
web_search = "live"

[model_providers.mcptap]
name = "routed-via-mcptap"
base_url = "http://127.0.0.1:8787/v1"
wire_api = "responses"
http_headers = { "X-Title" = "OpenAI Codex" }
supports_websockets = false

[memories]
extract_model = "openai/gpt-5.5"
consolidation_model = "openai/gpt-5.5"
```

## Hermes Agent configuration

Example Hermes Agent configuration:

```yaml
model:
  [...]
  provider: custom
  base_url: http://127.0.0.1:8787/v1
  api_mode: codex_responses
```

## Features

* [1. Model forcing](docs/FEATURES.md#1-model-forcing)
* [2. Plan mode model](docs/FEATURES.md#2-plan-mode-model)
* [3. OpenRouter provider controls](docs/FEATURES.md#3-openrouter-provider-controls)
* [4. Requesty notes](docs/FEATURES.md#4-requesty-notes)
* [5. MCP tool interception](docs/FEATURES.md#5-mcp-tool-interception)
  * [Enable MCP interception](docs/FEATURES.md#enable-mcp-interception)
  * [Example `mcp-intercept.yaml`](docs/FEATURES.md#example-mcp-interceptyaml)
* [6. Per-model instructions](docs/FEATURES.md#6-per-model-instructions)
* [7. Tool-call hook](docs/FEATURES.md#7-tool-call-hook)
  * [How it works](docs/FEATURES.md#how-it-works)
  * [Enable the tool-call hook](docs/FEATURES.md#enable-the-tool-call-hook)
  * [Hook contract](docs/FEATURES.md#hook-contract)
  * [Example `use_tool_hook.py`](docs/FEATURES.md#example-use_tool_hookpy)
  * [RTK integration via `updated_tool_calls`](docs/FEATURES.md#rtk-integration-via-updated_tool_calls)
  * [File access blocking setup](docs/FEATURES.md#file-access-blocking-setup)
  * [File-block limitations and configuration](docs/FEATURES.md#file-block-limitations-and-configuration)
* [8. Session tracking](docs/FEATURES.md#8-session-tracking)
* [9. UI interface](docs/FEATURES.md#9-ui-interface)
* [10. Logging](docs/FEATURES.md#10-logging)

## Service management

### Linux

Start:

```sh
systemctl --user start mcptap.service
```

Restart:

```sh
systemctl --user restart mcptap.service
```

Stop:

```sh
systemctl --user stop mcptap.service
```

Status:

```sh
systemctl --user status mcptap.service
```

Logs:

```sh
journalctl --user -u mcptap.service -f
```

### macOS

MCPTap is installed as a launchd user service:

```text
pl.pcode.mcptap
```

Restart:

```sh
launchctl kickstart -k "gui/$(id -u)/pl.pcode.mcptap"
```

Logs:

```sh
tail -f ~/Library/Logs/mcptap.log ~/Library/Logs/mcptap.error.log
```

### Manual start

If the service is not installed, run MCPTap manually:

```sh
mcptap
```

or:

```sh
~/.local/bin/mcptap
```

## Health endpoint

Check it with:

```sh
curl http://127.0.0.1:8787/health
```

## Troubleshooting

See [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) for common issues and their solutions.

## Security notes

If `MCPTAP_LOG_FILE` is enabled, consider:

```env
LOG_FILE_REDACT_HEADERS=1
```

MCP tools are executed locally with the permissions of the MCPTap process.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for development setup, running locally, and linting configuration.

## Issues

The list of issues that MCPTap addresses (at least partially): [docs/ISSUES.md](docs/ISSUES.md).

## Configuration reference

### `proxy.env`

| Variable | Default Value | Description |
| --- | ---: | --- |
| `MCPTAP_UPSTREAM_PROVIDER` | required | `openrouter`, `requesty`, `meta`, `nano-gpt`, or `llmtr`. |
| `MCPTAP_LISTEN_HOST` | `127.0.0.1` | Local host/interface to bind. |
| `MCPTAP_LISTEN_PORT` | `8787` | Local port to listen on. |
| `MCPTAP_OPENROUTER_PROVIDER` | empty | Optional OpenRouter provider slug. |
| `MCPTAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS` | `1` | Disable OpenRouter provider fallback when true. |
| `MCPTAP_PLAN_MODE_TRIGGER` | `max` | Reasoning effort value that activates plan mode model. |
| `MCPTAP_PLAN_MODE_MAX_INPUT_SIZE` | `300000` | Maximum accepted input size for plan mode. |
| `MCPTAP_INTERCEPT_YAML` | empty | MCP interception YAML or `@/path/to/file.yaml`. |
| `MCPTAP_INTERCEPT_MAX_ITERATIONS` | `8` | Maximum hidden tool-call loop iterations. |
| `MCPTAP_INTERCEPT_TOOL_TIMEOUT` | `120` | Timeout for one MCP tool call, in seconds. |
| `MCPTAP_PER_MODEL_YAML` | empty | Per-model instruction YAML or `@/path/to/file.yaml`. |
| `MCPTAP_USE_TOOL_HOOK` | empty | Path to a Python hook script run before client tool calls. |
| `MCPTAP_USE_TOOL_HOOK_TIMEOUT` | `30` | Timeout for the hook script, in seconds. |
| `MCPTAP_USE_TOOL_HOOK_SYNTHETIC_TOOL` | `get_goal` | Synthetic tool name to inject before the hook. Empty = direct mode. |
| `MCPTAP_PER_SESSION_DIR` | `/tmp/mcptap/per_session` | Directory for per-session control files. |
| `MCPTAP_LOG_LEVEL` | `INFO` | Python logging level. |
| `MCPTAP_LOG_FILE` | empty | Optional communication log file path. |
| `LOG_FILE_REDACT_HEADERS` | `0` | Redact sensitive headers in communication logs when true. |

### `openrouter.env`, `requesty.env`, `meta.env`, `nano-gpt.env`, `llmtr.env`

| Variable | Required | Description |
| --- | ---: | --- |
| `MCPTAP_API_KEY` | yes | Upstream provider API key. |
| `MCPTAP_MODEL` | yes | Default forced model. |
| `MCPTAP_PLAN_MODE_MODEL` | yes | Forced model used when plan mode is active. |
| `MCPTAP_USE_CHAT_COMPLETIONS` | no | Convert Responses requests to Chat Completions for providers/models that require it. |

### `LD_PRELOAD` file-block library

| Variable                            |             Default Value | Description                                                                                                               |
| ----------------------------------- | ------------------------: | ------------------------------------------------------------------------------------------------------------------------- |
| `MCPTAP_FB_DIR`                     | `/tmp/mcptap/per_session` | Directory for per-session control files.                                                                                  |
| `MCPTAP_FB_INTERPRETERS`            |        see `file_block.c` | Colon-separated list of basenames treated as interpreters.                                                                |
| `MCPTAP_FB_ESCALATORS`              |        see `file_block.c` | Colon-separated list of `argv[0]` basenames treated as privilege-escalators.                                              |
| `MCPTAP_FB_DISABLE_ESCALATOR_CHECK` |                     unset | When set to `1`, the surgical escalator+interpreter layer is disabled entirely (only the path-scan layer remains active). |
