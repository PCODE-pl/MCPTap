# FEATURES

## 1. Model forcing

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

## 2. Plan mode model

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

## 3. OpenRouter provider controls

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

## 4. Requesty notes

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

## 5. MCP tool interception

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

## 6. Per-model instructions

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

## 7. Tool-call hook

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
``allow`` response. MCPTap writes the list to a per-session control file at
``<MCP_TAP_PER_SESSION_DIR>/<session_id>/blocked_files``. The LD_PRELOAD library
(loaded once when Codex starts) reads this file automatically and blocks
access to the listed files at the libc level. No per-command prefixing is
needed — the library is active for the entire Codex session.

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

or with tool call argument rewriting:

```json
{"action": "allow", "updated_tool_calls": [
  {"call_id": "fc_abc123", "name": "exec_command", "arguments": {"cmd": "rtk git status"}}
]}
```

or:

```json
{"action": "block", "message": "Instruction for the model"}
```

If the hook times out, exits with a non-zero code, or returns invalid JSON,
MCPTap stops the turn with a ``use_tool_hook_error`` and the tool calls are
not executed.

#### ``updated_tool_calls``

When ``updated_tool_calls`` is present in an ``allow`` response, MCPTap
rewrites the matching tool call arguments in the model's response before
returning it to the client. Each entry must contain a ``call_id`` and may
override ``name`` and/or ``arguments``. Arguments may be provided as a dict
or a JSON string.

This enables transparent command rewriting: the model emits a normal tool
call (e.g. ``exec_command`` with ``cmd: "git status"``), the hook rewrites the
command (e.g. to ``rtk git status``), and the client receives and executes the
rewritten version. The model never knows its command was modified, and the
client sees the rewritten command in its UI.

Use cases:

* wrap shell commands with a token-compression proxy such as
  [RTK](https://github.com/rtk-ai/rtk) for 60–90% token savings,
* inject environment variables or flags into commands,
* normalize command names across different clients.

The rewrite works in both synthetic tool mode and direct hook mode, and is
compatible with streaming SSE responses — MCPTap re-serializes the response
body when updates are applied.

When ``blocked_files`` is present in an ``allow`` response, MCPTap writes the
list to a per-session control file at
``<MCP_TAP_PER_SESSION_DIR>/<session_id>/blocked_files``. The
``libmcptap_fileblock.so`` library (loaded via ``LD_PRELOAD`` when Codex
starts) reads this file automatically. It identifies the session via the
``CODEX_THREAD_ID`` environment variable (set by Codex CLI for all child
processes) and constructs the control file path as
``<MCPTAP_BLOCKED_DIR>/<CODEX_THREAD_ID>/blocked_files``. The library
intercepts ``open``, ``openat``, ``fopen``, ``access``, ``stat``, ``lstat``,
``statx``, ``readlink``, and ``realpath`` calls, returning ``EACCES`` for
blocked paths. The control file is reloaded every second, so changes take
effect immediately.

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
it also blocks access to sensitive files via the LD_PRELOAD library.

### RTK integration via ``updated_tool_calls``

The example hook script installed by MCPTap includes optional
[RTK](https://github.com/rtk-ai/rtk) (Rust Token Killer) integration. When the
``rtk`` binary is on ``PATH`` and meets the minimum version (0.23.0+), the hook
automatically rewrites shell commands in tool calls through ``rtk rewrite``,
reducing token consumption by 60–90%.

```text
Model calls: exec_command(cmd="git status")
  → hook detects rtk on PATH
  → hook calls: rtk rewrite "git status" → "rtk git status"
  → hook returns: updated_tool_calls: [{call_id: ..., arguments: {cmd: "rtk git status"}}]
  → MCPTap rewrites the response
  → client executes: rtk git status (compressed output)
```

To enable:

1. Install RTK: ``brew install rtk`` or
   ``curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh``
2. Verify: ``rtk --version``
3. The hook script auto-detects RTK — no configuration change needed.

The hook checks ``rtk --version`` on each invocation and gracefully skips
rewriting if RTK is absent or too old, so the hook works with or without RTK
installed.

### File access blocking setup

1/ Build the LD_PRELOAD library:

```shell
cd mcp-tap/file_block
make
make install  # installs to ~/.local/lib/libmcptap_fileblock.so
```

2/ Configure MCPTap:

```env
MCP_TAP_USE_TOOL_HOOK=/home/user/.config/mcptap/use_tool_hook.py
# Optional: use direct hook mode (no synthetic get_goal call)
MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL=
```

3/ Start Codex CLI with ``LD_PRELOAD`` set once:

```shell
LD_PRELOAD=~/.local/lib/libmcptap_fileblock.so codex --profile=mcptap
```

Or add an alias to your shell configuration:

```shell
alias codex='LD_PRELOAD=~/.local/lib/libmcptap_fileblock.so codex'
```

The library is inherited by all child processes (shell commands, scripts, etc.)
for the entire Codex session. It identifies the session via the
``CODEX_THREAD_ID`` environment variable (set automatically by Codex CLI) and
reads the control file at
``/tmp/mcptap/per_session/<CODEX_THREAD_ID>/blocked_files``.

4/ In your hook script, return ``blocked_files`` in the ``allow`` response.
   MCPTap writes them to the per-session control file. The library reloads
   the file every second, so blocking takes effect immediately.

### File-block limitations and configuration

The LD_PRELOAD library blocks file access by intercepting libc syscalls in
the **parent process** (Codex and its shell children). Because `glibc`
drops `LD_PRELOAD` libraries when executing a **setuid** binary (such as
`sudo`, `su`, `doas`, `pkexec`), a child started via an escalator runs
**without** the library and would otherwise be able to read a blocked file.

To close that escape vector, the library also intercepts `execve`,
`execv`, `execvp`, `execvpe`, `posix_spawn`, and `posix_spawnp` in the
parent and inspects `argv` before the child is spawned. This happens in
two layers:

1. **Path-scan** — each `argv[i>=1]` is resolved (tilde expansion, CWD
   join, symlink resolution) and compared against the blocklist. This
   blocks `sudo cat <blocked>`, `sudo cp <blocked> dst`, etc., where an
   argument **is** the blocked path.
2. **Surgical escalator + interpreter + payload scan** — when `argv[0]`
   is a privilege-escalator (`sudo`, `su`, `doas`, `pkexec`, `runuser`,
   `gksu`, ...) and a later argument is an interpreter (`bash`, `sh`,
   `python3`, `perl`, `xargs`, `dd`, ...), the concatenated **payload**
   (everything after the interpreter) is searched for a blocklist path
   as a substring (with `~` expanded anywhere in the payload). This
   blocks `sudo bash -c 'cat ~/.fzf-history'`,
   `sudo python3 -c "open('/home/u/.secret').read()"`, etc., without
   disabling `sudo bash -c 'systemctl restart nginx'` (whose payload
   contains no blocked path).

**Known limitations** (the surgical layer is **not** a complete sandbox;
these escapes are accepted as documented constraints of user-space
`LD_PRELOAD` interception and require a system-level mechanism such as
Landlock, AppArmor, or removal of `NOPASSWD` sudoers entries to fully
close):

* **Stdin / heredoc payloads** — a blocked path passed through a pipe or
  heredoc instead of `argv` is not visible to the interceptor:

  ```sh
  echo 'cat ~/.fzf-history' | sudo bash          # NOT blocked
  echo ~/.fzf-history   | sudo xargs cat          # NOT blocked
  sudo bash <<EOF                                 # NOT blocked
  cat ~/.fzf-history
  EOF
  ```

  The library only sees `argv`; the file path never appears there.

* **Obfuscated paths** — a payload that reconstructs the blocked path at
  runtime defeats the static substring scan:

  ```sh
  sudo bash -c 'F=~/.fzf-; F=${F}history; cat "$F"'   # NOT blocked
  sudo bash -c 'cat $(echo L2hvbWUv...cngK | base64 -d)'  # NOT blocked
  ```

  Analyzing shell semantics in user space is infeasible.

* **Path-normalization aliases** — while `./`, `../`, and symlinks are
  resolved for the path-scan layer, the surgical payload scan uses
  substring matching against the **expanded** form, so exotic aliasing
  (`//`, redundant `/./`, Unicode look-alikes) inside a payload string
  may also evade it.

**Configuration via environment variables** (all optional):

| Variable | Default | Effect |
| --- | --- | --- |
| `MCPTAP_FB_ESCALATORS` | built-in list (`sudo`, `su`, `doas`, `pkexec`, `runuser`, `gksu`, `gksudo`, `sudoedit`, ...) | Colon-separated list of `argv[0]` basenames treated as privilege-escalators. When set, it **overrides** the defaults (an empty value falls back to defaults). |
| `MCPTAP_FB_INTERPRETERS` | built-in list (`bash`, `sh`, `dash`, `python3`, `perl`, `xargs`, `dd`, `env`, `tee`, ...) | Colon-separated list of basenames treated as interpreters. When set, it **overrides** the defaults (an empty value falls back to defaults). |
| `MCPTAP_FB_DISABLE_ESCALATOR_CHECK` | unset | When set to `1`, the surgical escalator+interpreter layer is disabled entirely (only the path-scan layer remains active). |
| `MCPTAP_FB_PROCESS_ALLOWLIST` | `git:ssh` | Colon-separated list of process names (from `/proc/self/comm`) that bypass all blocklist checks. Allows trusted tools like `git` and `ssh` to access blocked paths (e.g. SSH keys for `git push`) while still blocking direct reads by the model. Set to empty string to disable the allowlist entirely. |

For complete sandboxing of setuid escape vectors, combine the library
with a system-level mechanism (Landlock on kernel ≥ 5.13, an AppArmor
profile for `/usr/bin/sudo`, or removal of `NOPASSWD: ALL` from
`sudoers`) — these operate at the kernel level and are not subject to
the `argv`-only visibility constraint of `LD_PRELOAD`.

## 8. Session tracking

MCPTap tracks session token usage and elapsed time per session. The session ID
is extracted from the ``session-id`` request header. If the ID is a UUIDv7,
the embedded timestamp is used as the session start time; otherwise the first
request time is used.

Token usage is accumulated from ``usage.total_tokens`` in upstream responses.
The counter resets when MCPTap restarts.

## 9. UI interface

MCPTap includes a built-in web-based log viewer that lets you inspect every
proxied request in real time.

### Access

The UI is served directly by the proxy on the same host and port as the
API traffic:

```text
http://<MCP_TAP_LISTEN_HOST>:<MCP_TAP_LISTEN_PORT>/ui/logs
```

No separate process or additional dependency is required — the page is
embedded in the proxy as a single-file Vue 3 + Naive UI application.

### What the UI shows

The log viewer displays a dark-themed, virtual-scroll table with one row per
proxied request:

| Column        | Source field        |
| ------------- | ------------------- |
| Date          | `timestamp`         |
| Model         | `model`             |
| Provider      | `provider`          |
| Input tokens  | `input_tokens`      |
| Output tokens | `output_tokens`     |
| Cost          | `cost`              |

A time-range selector lets you filter the view:

| Preset            | Value   |
| ----------------- | ------- |
| Past 15 minutes   | `15m`   |
| Past 30 Minutes   | `30m`   |
| Past 1 hour       | `1h`    |
| Past 3 hours      | `3h`    |
| Past 24 hours     | `24h`   |
| Past 48 hours     | `48h`   |
| Past 1 week       | `1w`    |

Rows are loaded newest-first with cursor-based pagination. Scrolling to the
bottom of the table automatically fetches the next page.

### Request detail drawer

Clicking any row opens a side drawer with the full request metadata:

* timestamp, model, provider, session ID, HTTP status code
* whether the response was streamed
* input / output / total token breakdown
* cost
* request path and duration in milliseconds
* full request body (pretty-printed JSON)
* full response body (pretty-printed JSON)

### Backing API

The UI consumes two REST endpoints exposed by the proxy:

```text
GET /api/logs?range=1h&limit=50&before=<unix-timestamp>
```

Returns a paginated list of log entries (newest first).  `before` is an
optional cursor — pass the timestamp of the last row in the current page to
fetch the next page.  `limit` defaults to 50 and is capped at 200.

```text
GET /api/logs/{log_id}
```

Returns the full detail of a single log entry including request and response
bodies.

### Storage

All log data is persisted in a local SQLite database (WAL mode):

```env
MCP_TAP_LOG_DB=/home/user/.local/share/mcptap/logs.db
```

If the variable is unset, the default path is used:

```text
~/.local/share/mcptap/logs.db
```

The database schema is created automatically on startup via forward-only
migrations.  Each record stores:

| Field            | Description                              |
| ---------------- | ---------------------------------------- |
| `timestamp`      | Unix timestamp of the request            |
| `session_id`     | Session ID from the `session-id` header  |
| `model`          | Forced model used                        |
| `provider`       | Upstream provider                        |
| `input_tokens`   | Prompt tokens from upstream `usage`      |
| `output_tokens`  | Completion tokens from upstream `usage`  |
| `total_tokens`   | Total tokens from upstream `usage`       |
| `cost`           | Cost reported by upstream                |
| `status_code`    | HTTP status returned to the client       |
| `request_body`   | Full request payload (JSON)              |
| `response_body`  | Full response body (JSON or SSE)         |
| `request_path`   | Upstream API path                        |
| `stream`         | Whether the response was streamed        |
| `duration_ms`    | Round-trip duration                      |

## 10. Logging

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
