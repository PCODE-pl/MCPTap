# MCPTap

MCPTap is a local LLM proxy for OpenAI-compatible clients using the Responses API.

It sits between an AI client, such as Codex CLI, and an upstream LLM gateway, such as OpenRouter or Requesty. It can force selected models, route requests through a chosen provider, log traffic for debugging, and expose selected MCP tools (with name aliasing) to the model while keeping intercepted MCP tool calls hidden from the client.

Works around issues that are, hopefully, temporary: in some configurations, the current version of Codex CLI does not see tools exposed directly by MCP servers. With a proxy layer, these calls can be intercepted, logged, aliased, and controlled more easily (see examples folder).

In practice, MCPTap is useful when you want more control over how an agent talks to models and tools without modifying the agent itself.

## What MCPTap does

MCPTap can:

* force all requests to use a configured model,
* use a different model for plan mode,
* route traffic to OpenRouter or Requesty,
* pin or restrict OpenRouter provider routing,
* intercept selected MCP tool calls,
* execute real MCP tools locally through stdio,
* feed MCP tool results back into the model,
* return only the final assistant response to the client,
* log upstream requests and responses,
* support normal JSON responses and streaming SSE responses,
* expose a local health endpoint,
* run a configurable hook before client tool calls to allow or block them.

## High-level flow

```text
AI client
  │
  │ OpenAI-compatible /v1/responses request
  ▼
MCPTap
  │
  │ rewritten / routed request
  ▼
OpenRouter or Requesty
  │
  │ model response
  ▼
MCPTap
  │
  ├─ if the model calls an intercepted tool:
  │      MCPTap calls the configured MCP server locally
  │      and sends the tool result back to the model
  │
  └─ final response
       ▼
     AI client
```

The client does not need to know that an intercepted MCP call happened. From the client's perspective, it receives a normal final model response.

## Main use cases

MCPTap is designed for workflows like:

* using Codex CLI through OpenRouter or Requesty,
* forcing a cheaper model for normal work and a stronger model for planning,
* giving a weaker model access to a stronger “expert” model through an MCP tool,
* hiding complex MCP orchestration from the client,
* debugging model/tool traffic,
* testing provider fallback behavior,
* controlling OpenRouter provider selection.

## Requirements

MCPTap requires:

* Python 3.10 or newer,
* `curl` or `wget` for installation,
* an OpenRouter or Requesty API key,
* optionally, an MCP server if you want MCP tool interception.

Runtime Python dependencies:

```text
aiohttp
python-dotenv
mcp
pyyaml
```

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
~/.local/share/mcptap       application files
~/.local/bin/mcptap         executable wrapper
~/.config/mcptap            configuration files
```

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
mcp-intercept.yaml optional MCP tool interception configuration
per-model.yaml     optional per-model instruction overrides
```

## Quick start

### 1. Select the upstream provider

Edit:

```sh
~/.config/mcptap/proxy.env
```

Example for OpenRouter:

```env
MCP_TAP_UPSTREAM_PROVIDER=openrouter
MCP_TAP_LISTEN_HOST=127.0.0.1
MCP_TAP_LISTEN_PORT=8787
```

Example for Requesty:

```env
MCP_TAP_UPSTREAM_PROVIDER=requesty
MCP_TAP_LISTEN_HOST=127.0.0.1
MCP_TAP_LISTEN_PORT=8787
```

Supported upstream providers:

```text
openrouter
requesty
```

### 2. Configure the provider

For OpenRouter, edit:

```sh
~/.config/mcptap/openrouter.env
```

Example:

```env
MCP_TAP_API_KEY=sk-or-v1-...
MCP_TAP_MODEL=deepseek/deepseek-v4-flash:floor
MCP_TAP_PLAN_MODE_MODEL=z-ai/glm-5.2:floor
```

For Requesty, edit:

```sh
~/.config/mcptap/requesty.env
```

Example:

```env
MCP_TAP_API_KEY=rqsty-sk-...
MCP_TAP_MODEL=nvidia/nemotron-3-nano-30b-a3b:free
MCP_TAP_PLAN_MODE_MODEL=zai/glm-5.2:floor
```

### 3. Restart MCPTap

Linux:

```sh
systemctl --user restart mcptap.service
```

macOS:

```sh
launchctl kickstart -k "gui/$(id -u)/pl.pcode.mcptap"
```

### 4. Check the health endpoint

```sh
curl http://127.0.0.1:8787/health
```

Example response shape:

```json
{
  "status": "ok",
  "upstream": "https://openrouter.ai/api/v1",
  "forced_model": "deepseek/deepseek-v4-flash",
  "forced_provider": null,
  "provider_fallbacks_disabled": false,
  "mcp_intercept": null,
  "per_model_config": {}
}
```

## Codex configuration

Example Codex configuration:

```toml
model_provider = "mcptap"
model = "openai/gpt-5.5"
model_context_window = 1000000

# This value must be different from MCP_TAP_PLAN_MODE_TRIGGER.
# For this reasoning effort, MCPTap will use MCP_TAP_MODEL
# from the selected provider env file.
model_reasoning_effort = "xhigh"

# This value must match MCP_TAP_PLAN_MODE_TRIGGER.
# For this reasoning effort, MCPTap will use MCP_TAP_PLAN_MODE_MODEL
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

The important parts are:

```toml
base_url = "http://127.0.0.1:8787/v1"
wire_api = "responses"
```

MCPTap's MCP interception loop is designed for `/v1/responses`.

## Model forcing

MCPTap rewrites incoming JSON requests that contain a `model` field.

The client may send:

```json
{
  "model": "some-client-model"
}
```

MCPTap rewrites it to the configured model:

```env
MCP_TAP_MODEL=deepseek/deepseek-v4-flash:floor
```

This lets you keep client configuration stable while changing the actual upstream model from MCPTap's config files.

## Plan mode model

MCPTap can use a different model when the request has a configured reasoning effort.

Default trigger:

```env
MCP_TAP_PLAN_MODE_TRIGGER=max
```

Normal model:

```env
MCP_TAP_MODEL=deepseek/deepseek-v4-flash:floor
```

Plan mode model:

```env
MCP_TAP_PLAN_MODE_MODEL=z-ai/glm-5.2:floor
```

When the incoming payload contains:

```json
{
  "reasoning": {
    "effort": "max"
  }
}
```

MCPTap switches from `MCP_TAP_MODEL` to `MCP_TAP_PLAN_MODE_MODEL`.

You can also limit plan mode input size:

```env
MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE=100000
```

If the input is larger than the configured limit, MCPTap returns an error instead of forwarding the request.

## OpenRouter provider controls

When using OpenRouter, MCPTap can control provider routing.

Optional provider pinning:

```env
MCP_TAP_OPENROUTER_PROVIDER=
```

Set it to an OpenRouter provider slug to force only that provider:

```env
MCP_TAP_OPENROUTER_PROVIDER=some-provider-slug
```

Disable provider fallback:

```env
MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS=1
```

Allow provider fallback:

```env
MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS=0
```

MCPTap also removes incoming `models` fallback configuration from the client payload so the configured forced model remains the only selected model.

## Requesty notes

When using Requesty:

```env
MCP_TAP_UPSTREAM_PROVIDER=requesty
```

MCPTap forwards requests to:

```text
https://router.requesty.ai/v1
```

For OpenAI model IDs sent to Requesty, MCPTap automatically adjusts model vendor naming for the Responses API by adding the `-responses` vendor variant when needed.

For example:

```text
openai/gpt-5.5
```

may be rewritten internally to:

```text
openai-responses/gpt-5.5
```

MCPTap also strips trailing model suffix descriptors such as `:floor` before sending the final model name upstream where needed.

## MCP tool interception

MCPTap can expose selected MCP tools to the model as normal function tools.

When the model calls one of those tools:

1. MCPTap detects the intercepted function call.
2. MCPTap calls the real MCP tool through a local stdio MCP server.
3. MCPTap serializes the MCP result.
4. MCPTap sends the tool result back to the model.
5. MCPTap repeats the loop until the model returns a final answer.
6. MCPTap returns only the final assistant response to the client.

The client never sees the intercepted tool calls.

### Enable MCP interception

In `proxy.env`:

```env
MCP_TAP_INTERCEPT_YAML=@/home/user/.config/mcptap/mcp-intercept.yaml
MCP_TAP_INTERCEPT_TOOL_TIMEOUT=300
MCP_TAP_INTERCEPT_MAX_ITERATIONS=8
```

The `@` prefix means: load YAML from this file path.

You may also put YAML directly in the environment variable, but using a file is usually easier.

### Example `mcp-intercept.yaml`

```yaml
mcp_command: /home/user/.venvs/llm-council/bin/llm-council-mcp
mcp_args: []
mcp_cwd: /home/user/projects/my-project
mcp_env:
  LLM_COUNCIL_CONFIG: /home/user/.config/llm-council/llm_council.yaml

mappings:
  - expose_as: consult_council
    mcp_tool: consult_council
    override:
      description: |
        Ask a stronger expert model for help when the current task is complex,
        ambiguous, or requires additional reasoning.
      parameters:
        type: object
        properties:
          question:
            type: string
            description: The concrete question to ask the expert model.
          context:
            type: string
            description: Relevant context to pass to the expert model.
        required:
          - question
```

Top-level fields:

| Field         | Description                                              |
| ------------- | -------------------------------------------------------- |
| `mcp_command` | Command used to start the MCP server.                    |
| `mcp_args`    | Optional command arguments.                              |
| `mcp_env`     | Optional environment variables passed to the MCP server. |
| `mcp_cwd`     | Optional working directory for the MCP server process.   |
| `mappings`    | List of MCP tools exposed to the model.                  |

Mapping fields:

| Field       | Description                                               |
| ----------- | --------------------------------------------------------- |
| `expose_as` | Tool name shown to the model.                             |
| `mcp_tool`  | Real MCP tool name called by MCPTap.                      |
| `override`  | Optional shallow override for description and parameters. |

MCPTap discovers the real MCP tool schema with `list_tools()`. If `override` is provided, it is merged on top of the MCP-derived definition.

MCPTap always keeps control over:

```text
type
execution
name
```

so an override cannot break MCPTap's internal routing.

## Per-model instructions

MCPTap can inject additional instructions based on the forced model.

Enable it in `proxy.env`:

```env
MCP_TAP_PER_MODEL_YAML=@/home/user/.config/mcptap/per-model.yaml
```

Example `per-model.yaml`:

```yaml
deepseek/deepseek-v4-flash:
  instructions: |
    Be concise. Use tools only when they are necessary.

z-ai/glm-5.2:
  instructions: |
    You are running in plan mode. Focus on analysis, risk detection,
    and producing a concrete execution plan.

"@preset/free-fallback-to-paid":
  instructions: |
    Prefer low-cost reasoning and avoid unnecessary verbose output.

policy/free-fallback-to-paid:
  instructions: |
    Keep answers compact unless the user explicitly asks for detail.
```

Notes:

* entries may use normal model names,
* model suffixes such as `:floor` are ignored for matching fallback,
* OpenRouter presets such as `@preset/name` are supported,
* Requesty policies such as `policy/name` are supported,
* instructions are injected only on the first request, not on follow-up requests using `previous_response_id`.

## Tool-call hook

MCPTap can run a configurable Python script before allowing the model's client
tool calls to execute. This lets you enforce policies based on session token
usage, elapsed time, or the current goal state.

### How it works

When the model returns client function calls (tools executed by the client, not
intercepted MCP tools), MCPTap has two modes:

**Synthetic tool mode** (default, ``MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL=get_goal``):

1. MCPTap saves the model's response.
2. MCPTap returns a synthetic ``get_goal`` function call to the client.
3. The client executes ``get_goal`` and sends the result back.
4. MCPTap runs the configured hook script, passing session info, the
   ``get_goal`` result, and the pending tool calls on stdin.
5. If the hook returns ``allow``, MCPTap returns the saved model response so
   the client can execute the tool calls.
6. If the hook returns ``block``, MCPTap feeds the block message back to the
   model and passes through the model's next response once without re-running
   the hook.

**Direct hook mode** (``MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL=`` empty):

1. MCPTap saves the model's response.
2. MCPTap runs the configured hook script immediately, passing session info
   and the pending tool calls on stdin (no synthetic tool call injected).
3. If the hook returns ``allow``, MCPTap returns the saved model response.
4. If the hook returns ``block``, MCPTap feeds the block message back to the
   model and passes through the model's next response once without re-running
   the hook.

In both modes, the hook can optionally return a ``blocked_files`` list in an
``allow`` response. When ``MCP_TAP_FILE_BLOCK_LIB`` is configured, MCPTap writes
the list to a control file and injects an instruction telling the model to
prefix all shell commands with ``LD_PRELOAD=<lib> MCPTAP_BLOCKED_FILES_FILE=<path>``.
This blocks access to the listed files at the libc level.

Hidden MCP intercepted tool calls (such as ``consult_council``) are excluded
from the hook. When the model returns mixed calls (intercepted and client),
MCPTap resolves the intercepted ones first and defers client calls to the hook.

### Enable the tool-call hook

In ``proxy.env``:

```env
MCP_TAP_USE_TOOL_HOOK=/home/user/.config/mcptap/use_tool_hook.py
MCP_TAP_USE_TOOL_HOOK_TIMEOUT=30
```

### Hook contract

The hook script receives a JSON object on stdin:

```json
{
  "session_id": "...",
  "forced_model": "...",
  "used_tokens": 12345,
  "used_time_seconds": 130.5,
  "get_goal_result": {},
  "tool_calls": [
    {"call_id": "...", "name": "...", "arguments": {}}
  ]
}
```

The hook must print a JSON decision on stdout:

```json
{"action": "allow"}
```

or with file access blocking:

```json
{"action": "allow", "blocked_files": ["/path/to/secret.py", "~/.git-credentials"]}
```

or:

```json
{"action": "block", "message": "Instruction for the model"}
```

If the hook times out, exits with a non-zero code, or returns invalid JSON,
MCPTap stops the turn with a ``use_tool_hook_error`` and the tool calls are
not executed.

When ``blocked_files`` is present in an ``allow`` response and
``MCP_TAP_FILE_BLOCK_LIB`` points to the ``libmcptap_fileblock.so`` shared
library, MCPTap writes the list to a per-session control file and injects an
instruction into the response telling the model to use ``LD_PRELOAD`` for all
shell commands. The library intercepts ``open``, ``openat``, ``fopen``,
``access``, ``stat``, ``lstat``, ``statx``, ``readlink``, and ``realpath``
calls, returning ``EACCES`` for blocked paths.

### Example ``use_tool_hook.py``

```python
import json
import sys

SENSITIVE_FILES = ["~/.git-credentials", "~/.ssh/id_rsa"]

data = json.load(sys.stdin)
used_tokens = data.get("used_tokens", 0)
used_time = data.get("used_time_seconds", 0.0)

if used_tokens > 10000 or used_time > 120:
    print(json.dumps({
        "action": "block",
        "message": "Use consult_council to review your approach.",
    }))
else:
    print(json.dumps({
        "action": "allow",
        "blocked_files": SENSITIVE_FILES,
    }))
```

This example blocks tool calls when the session exceeds 10000 tokens or 120
seconds, instructing the model to use ``consult_council`` first. When allowed,
it also blocks access to sensitive files via LD_PRELOAD.

### File access blocking setup

1. Build the LD_PRELOAD library:

```shell
cd mcp-tap/file_block
make
make install  # installs to ~/.local/lib/libmcptap_fileblock.so
```

2. Configure MCPTap:

```env
MCP_TAP_USE_TOOL_HOOK=/home/user/.config/mcptap/use_tool_hook.py
MCP_TAP_FILE_BLOCK_LIB=/home/user/.local/lib/libmcptap_fileblock.so
# Optional: use direct hook mode (no synthetic get_goal call)
MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL=
```

3. In your hook script, return ``blocked_files`` in the ``allow`` response.
   The library reads the control file (specified by
   ``MCPTAP_BLOCKED_FILES_FILE``) and blocks access to listed paths.

### Session tracking

MCPTap tracks session token usage and elapsed time per session. The session ID
is extracted from the ``session-id`` request header. If the ID is a UUIDv7,
the embedded timestamp is used as the session start time; otherwise the first
request time is used.

Token usage is accumulated from ``usage.total_tokens`` in upstream responses.
The counter resets when MCPTap restarts.

## Logging

Runtime logs on Linux:

```sh
journalctl --user -u mcptap.service -f
```

Debug logs on Linux:

```sh
journalctl --user -u mcptap.service -p debug -f
```

macOS logs:

```sh
tail -f ~/Library/Logs/mcptap.log ~/Library/Logs/mcptap.error.log
```

You can also enable communication logging to a file:

```env
MCP_TAP_LOG_FILE=/tmp/mcptap.log
```

Set log level:

```env
MCP_TAP_LOG_LEVEL=INFO
```

or:

```env
MCP_TAP_LOG_LEVEL=DEBUG
```

Redact sensitive headers in communication logs:

```env
LOG_FILE_REDACT_HEADERS=1
```

Sensitive headers include:

```text
authorization
cookie
proxy-authorization
set-cookie
```

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

MCPTap exposes:

```text
http://127.0.0.1:8787/health
```

Check it with:

```sh
curl http://127.0.0.1:8787/health
```

The health endpoint reports:

* proxy status,
* upstream URL,
* forced model,
* forced OpenRouter provider,
* provider fallback setting,
* MCP interception state,
* resolved MCP mappings,
* loaded per-model configuration.

## Troubleshooting

### `MCP_TAP_UPSTREAM_PROVIDER must be one of 'openrouter' or 'requesty'`

Check `proxy.env`:

```env
MCP_TAP_UPSTREAM_PROVIDER=openrouter
```

or:

```env
MCP_TAP_UPSTREAM_PROVIDER=requesty
```

### `MCP_TAP_API_KEY must not be empty`

Set your API key in the selected provider file:

```sh
~/.config/mcptap/openrouter.env
```

or:

```sh
~/.config/mcptap/requesty.env
```

Example:

```env
MCP_TAP_API_KEY=sk-or-v1-...
```

### `MCP_TAP_MODEL and MCP_TAP_PLAN_MODE_MODEL must not be empty`

Set both variables in the selected provider file:

```env
MCP_TAP_MODEL=deepseek/deepseek-v4-flash:floor
MCP_TAP_PLAN_MODE_MODEL=z-ai/glm-5.2:floor
```

### Health endpoint does not respond

Check whether the service is running:

```sh
systemctl --user status mcptap.service
```

Check logs:

```sh
journalctl --user -u mcptap.service -f
```

Also verify the configured host and port:

```env
MCP_TAP_LISTEN_HOST=127.0.0.1
MCP_TAP_LISTEN_PORT=8787
```

### MCP tool is not resolved

Check the health endpoint:

```sh
curl http://127.0.0.1:8787/health
```

Look at the `mcp_intercept.mappings` section. If `resolved` is `false`, MCPTap could not find the configured `mcp_tool` in the MCP server's `list_tools()` response.

Verify:

* `mcp_command`,
* `mcp_args`,
* `mcp_cwd`,
* `mcp_env`,
* the real MCP tool name,
* that the MCP server starts correctly outside MCPTap.

### Request fails only in plan mode

Check:

```env
MCP_TAP_PLAN_MODE_TRIGGER=max
MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE=100000
```

If the request input is larger than the configured limit, MCPTap rejects it before forwarding.

### Streaming issues

MCPTap supports streaming SSE responses. For intercepted `/v1/responses` calls, MCPTap may buffer upstream events internally so it can detect hidden function calls, execute MCP tools, and only then return the correct final response to the client.

If you are debugging streaming behavior, enable:

```env
MCP_TAP_LOG_LEVEL=DEBUG
MCP_TAP_LOG_FILE=/tmp/mcptap.log
```

## Security notes

MCPTap runs locally, but it has access to:

* your upstream API key,
* request and response payloads,
* configured MCP servers,
* local environment variables passed to MCP tools.

Be careful with:

* committing real `.env` files,
* enabling verbose communication logs,
* passing secrets through `mcp_env`,
* exposing MCPTap on anything other than localhost.

Recommended local binding:

```env
MCP_TAP_LISTEN_HOST=127.0.0.1
```

Avoid binding to public interfaces unless you add your own network-level protection.

If `MCP_TAP_LOG_FILE` is enabled, consider:

```env
LOG_FILE_REDACT_HEADERS=1
```

MCP tools are executed locally with the permissions of the MCPTap process. Only configure MCP servers you trust.

## Development

Clone the repository:

```sh
git clone https://github.com/PCODE-pl/MCPTap.git
cd MCPTap
```

Create a virtual environment:

```sh
python3.10 -m venv .venv
. .venv/bin/activate
```

Install dependencies:

```sh
pip install -r requirements.txt
```

Run locally:

```sh
python proxy.py
```

Format and linting are configured with Ruff.

The current Ruff configuration uses:

```text
line length: 120
quote style: double
indent style: space
lint rules: E, F, W, Q, I
ignored rules: E203, E501
```

## Configuration reference

### `proxy.env`

| Variable                                        |     Default | Description                                               |
| ----------------------------------------------- | ----------: | --------------------------------------------------------- |
| `MCP_TAP_UPSTREAM_PROVIDER`                     |    required | `openrouter` or `requesty`.                               |
| `MCP_TAP_LISTEN_HOST`                           | `127.0.0.1` | Local host/interface to bind.                             |
| `MCP_TAP_LISTEN_PORT`                           |      `8787` | Local port to listen on.                                  |
| `MCP_TAP_OPENROUTER_PROVIDER`                   |       empty | Optional OpenRouter provider slug.                        |
| `MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS` |         `1` | Disable OpenRouter provider fallback when true.           |
| `MCP_TAP_PLAN_MODE_TRIGGER`                     |       `max` | Reasoning effort value that activates plan mode model.    |
| `MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE`              |    `300000` | Maximum accepted input size for plan mode.                |
| `MCP_TAP_INTERCEPT_YAML`                        |       empty | MCP interception YAML or `@/path/to/file.yaml`.           |
| `MCP_TAP_INTERCEPT_MAX_ITERATIONS`              |         `8` | Maximum hidden tool-call loop iterations.                 |
| `MCP_TAP_INTERCEPT_TOOL_TIMEOUT`                |       `120` | Timeout for one MCP tool call, in seconds.                |
| `MCP_TAP_PER_MODEL_YAML`                        |       empty | Per-model instruction YAML or `@/path/to/file.yaml`.      |
| `MCP_TAP_USE_TOOL_HOOK`                         |       empty | Path to a Python hook script run before client tool calls.|
| `MCP_TAP_USE_TOOL_HOOK_TIMEOUT`                 |        `30` | Timeout for the hook script, in seconds.                  |
| `MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL`          |  `get_goal` | Synthetic tool name to inject before the hook. Empty = direct mode. |
| `MCP_TAP_FILE_BLOCK_LIB`                        |       empty | Path to `libmcptap_fileblock.so` for LD_PRELOAD file blocking. |
| `MCP_TAP_BLOCKLIST_DIR`                         |  `/tmp/mcptap_blocklists` | Directory for per-session blocklist control files. |
| `MCP_TAP_LOG_LEVEL`                             |      `INFO` | Python logging level.                                     |
| `MCP_TAP_LOG_FILE`                              |       empty | Optional communication log file path.                     |
| `LOG_FILE_REDACT_HEADERS`                       |         `0` | Redact sensitive headers in communication logs when true. |

### `openrouter.env` and `requesty.env`

| Variable                  | Required | Description                                 |
| ------------------------- | -------: | ------------------------------------------- |
| `MCP_TAP_API_KEY`         |      yes | Upstream provider API key.                  |
| `MCP_TAP_MODEL`           |      yes | Default forced model.                       |
| `MCP_TAP_PLAN_MODE_MODEL` |      yes | Forced model used when plan mode is active. |

## Limitations

MCPTap is intentionally small and focused.

Current design assumptions:

* one configured upstream provider at a time,
* one MCP interception configuration per MCPTap instance,
* MCP server communication uses stdio,
* hidden MCP interception applies to `/v1/responses`,
* non-JSON or non-model requests are passed through,
* MCPTap is intended to run locally.

## Project status

MCPTap is an early-stage local proxy for experimenting with controlled model routing and MCP tool interception.

Use it carefully, especially when enabling communication logs or running MCP tools with access to local files, credentials, or shell commands.
