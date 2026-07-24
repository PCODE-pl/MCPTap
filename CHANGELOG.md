<!-- markdownlint-disable MD024 -->
# Changelog

## [2.5.0]

### Highlights

- Web-based request log viewer (Vue 3 + Naive UI, dark theme)
- SQLite-backed log store

### Added

- **Web-based request log viewer** — MCPTap now ships an embedded log viewer
  at `/ui/logs` (Vue 3 + Naive UI, dark theme).  The viewer displays a
  virtual-scroll table of all proxied requests with columns for date, model,
  provider, input/output tokens, and cost.  A time-range selector (15m – 1w)
  filters the view; cursor-based pagination with infinite scroll loads older
  entries automatically.  Clicking any row opens a side drawer with full
  request metadata (session ID, HTTP status, stream flag, token breakdown,
  cost, path, duration) and pretty-printed JSON request/response bodies.
- **SQLite-backed log store** — proxied request metadata is persisted in a
  local SQLite database (WAL mode) with forward-only schema migrations.
  Configurable via `MCP_TAP_LOG_DB` (default: `~/.local/share/mcptap/logs.db`).
  Records store timestamp, session ID, model, provider, input/output/total
  tokens, cost, HTTP status, full request/response bodies, request path,
  stream flag, and round-trip duration.
- **Log API endpoints** — two REST endpoints exposed by the proxy:
  `GET /api/logs` (paginated, time-range filtered, cursor-based) and
  `GET /api/logs/{log_id}` (full detail including request/response bodies).
- **Usage extraction** — `extract_usage_details()` parses `input_tokens`,
  `output_tokens`, `total_tokens`, and `cost` from upstream Responses API
  responses for storage in the log database.
- **Log-store tests** — 15 new tests in `tests/test_log_store.py` covering
  migrations, recording, paginated queries, detail lookup, and the
  `record_from_response` convenience wrapper.
- **`docs/FEATURES.md`** — new section 9 (UI interface) documenting the log
  viewer access URL, table columns, time-range presets, request detail
  drawer, backing API, and SQLite storage schema.
- **`docs/TROUBLESHOOTING.md`** — extracted from the README into a standalone
  troubleshooting guide.
- **`docs/DEVELOPMENT.md`** — new development documentation.

### Changed

- **README.md restructured** — rewritten and reorganized for clearer
  installation, configuration, routing, model-instruction, and tool-call-hook
  guidance; redundant sections removed and RTK link corrected.

### Fixed

- **Request details drawer width** — increased from 700px to 1200px for
  better visibility of detailed request data.
- **Log-store test execution** — `tests/test_log_store.py` re-enabled in the
  test runner (previously skipped); `pytest` import type-ignore added to
  `tests/test_config_reloader.py` to silence missing-stub noise.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.4.0...v2.5.0](https://github.com/PCODE-pl/MCPTap/compare/v2.4.0...v2.5.0)

## [2.4.0]

### Highlights

- Hot-reload for configuration files

### Added

- **Hot-reload for configuration files** — the proxy now polls mtime of
  configuration files (`proxy.env`, `openrouter.env`, `requesty.env`,
  `mcp-intercept.yaml`, `per-model.yaml`, `use_tool_hook.py`) every 2 seconds
  and triggers a selective reload cascade when any of them changes:

  - **env files** (`proxy.env`, `openrouter.env`, `requesty.env`) → full
    `Settings` reload + propagation to all dependent components (interceptor,
    per-model config, tool hook);
  - **`mcp-intercept.yaml`** → stop old MCP subprocess + start a new
    `MCPInterceptor` instance;
  - **`per-model.yaml`** → reload the per-model config dict;
  - **`use_tool_hook.py`** → reload the tool-hook enabled flag and `Settings`
    (path may change).

  The reloader runs as a background asyncio task inside the aiohttp event
  loop, wired into `on_startup`/`on_cleanup` lifecycle in `app.py`.

- **`_SettingsProxy` with `reload_settings()`** — `settings.py` was refactored
  so the module-level `settings` object is a transparent proxy that delegates
  `__getattr__`/`__setattr__` to the current `Settings` instance. Calling
  `reload_settings()` rebuilds the instance from env files and swaps the
  proxy target, so all callers automatically see reloaded values without
  re-importing.

- **Stale provider env key cleanup** — before loading a new provider env
  file, known provider keys (`MCP_TAP_API_KEY`, `MCP_TAP_MODEL`,
  `MCP_TAP_PLAN_MODE_MODEL`, `MCP_TAP_OPENROUTER_PROVIDER`,
  `MCP_TAP_OPENROUTER_DISABLE_PROVIDER_FALLBACKS`) are removed from
  `os.environ` to prevent stale values leaking across provider switches.

- **25 new config-reload tests** — `tests/test_config_reloader.py` (616 lines)
  covering `_SettingsProxy` delegation/swap semantics, stale env key cleanup,
  mtime detection, selective reload cascade routing, callback failures,
  lifecycle wiring, and application-level reload callbacks
  (`reload_per_model_config`, `reload_tool_hook`, `reload_intercept`,
  `reload_env_and_propagate`).

- **`markdownlint` pre-commit hook** — added a `markdownlint` hook to
  `.pre-commit-config.yaml`, wrapped by the shared `wrap_hook.sh` wrapper.
  The hook runs `markdownlint-cli2` on committed Markdown files at the
  `pre-commit` stage (`fail_fast: true`, `pass_filenames: false`).

- **`.markdownlint-cli2.jsonc`** — markdownlint CLI configuration that
  disables the `MD013` (line length) rule and enables auto-fix mode.

### Changed

- **`settings.py` refactored for hot-reload** — the monolithic
  `Settings.from_env()` classmethod was split into `_load_env_files()` (env
  loading + provider selection + stale key cleanup) and
  `_build_settings_from_env()` (reads `os.environ` into the dataclass).
  `from_env()` now delegates to both, preserving the original entry point.
  The `Settings` dataclass docstring and module docstring were updated to
  document the hot-reload mechanism.

- **Markdownlint directive in changelog-creator skill** — added a
  `<!-- markdownlint-disable-next-line MD034 -->` comment to the example
  invocation blockquote in `.agents/skills/changelog-creator/SKILL.md`.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.3.0...v2.4.0](https://github.com/PCODE-pl/MCPTap/compare/v2.3.0...v2.4.0)

## [2.3.0]

### Added

- **`mcptap` package** — the monolithic `proxy.py` (2346 lines) was split into
  focused modules: `app.py`, `upstream.py`, `response_flow.py`, `responses.py`,
  `rewrite.py`, `mcp_intercept.py`, `session.py`, `tool_hook.py`, `settings.py`,
  `http_utils.py`, and `file_block.py`. `proxy.py` is now a thin entry-point
  that imports from the package.

- **changelog-creator skill metadata** — added frontmatter (name, description)
  to `.agents/skills/changelog-creator/SKILL.md` for skill discovery.

### Changed

- **Public API without private prefixes** — helper functions previously named
  with `_` prefix (e.g. `_blocklist_file_path`, `_write_blocklist`,
  `_build_synthetic_tool_response`, `_extract_client_tool_calls`,
  `_apply_tool_call_updates`, `_re_serialize_response`, etc.) are now exposed
  as public functions in the `mcptap` package (e.g. `blocklist_file_path`,
  `write_blocklist`, `build_synthetic_tool_response`, etc.).

- **Tests updated** — `test_file_block.py` and `test_tool_hook.py` now import
  `mcptap.settings` and reference public function names instead of private
  `proxy._*` attributes.

- **`setup.sh` updated** — source validation and install now check for and
  copy the `mcptap/` directory alongside `proxy.py` and `examples/`.

### Fixed

- **`setup.sh` reinstall overwrites `mcptap/` correctly** — `cp -r` into an
  existing destination previously created a nested `mcptap/mcptap/` instead of
  replacing files. Now the old directory is removed first and `__pycache__` is
  cleaned after copy.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.2.0...v2.3.0](https://github.com/PCODE-pl/MCPTap/compare/v2.2.0...v2.3.0)

## [2.2.0]

### Added

- Process allowlist (MCPTAP_FB_PROCESS_ALLOWLIST) — the LD_PRELOAD
  file-block library now supports a colon-separated list of process names
  that bypass all blocklist checks. The process name is read from
  /proc/self/comm (Linux). Default allowlist: git:ssh. This enables
  git push / git commit to function when SSH keys or
  ~/.git-credentials are on the blocklist — git and ssh can read
  them, but direct reads by the model (cat, head, less, …) remain
  blocked. Set MCPTAP_FB_PROCESS_ALLOWLIST="" to disable the allowlist
  entirely.

- 5 new process-allowlist tests — TestProcessAllowlist covering:
  allowlisted process reads a blocked file, non-allowlisted process is
  blocked, default allowlist includes git, empty allowlist disables
  bypass, and multiple colon-separated entries are honored.

- Dependabot configuration — added .github/dependabot.yml for
  automated weekly dependency update PRs for both pip (Python  dependencies) and github-actions (workflow dependencies).

### Changed

- Removed ~/.gitconfig from SENSITIVE_FILES — the example
  use_tool_hook.py no longer blocks ~/.gitconfig (it contains no
  secrets — only user.name, user.email, aliases). SSH keys and
  ~/.git-credentials remain blocked.

- README updated — added MCPTAP_FB_PROCESS_ALLOWLIST to the
  environment variable configuration table.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.1.0...v2.2.0](https://github.com/PCODE-pl/MCPTap/compare/v2.1.0...v2.2.0)

## [2.1.0]

### Added

- updated_tool_calls hook response support — the tool-call hook can now
  return updated_tool_calls in an allow response to rewrite tool call
  arguments (e.g. wrap shell commands with RTK) before the response is returned
  to the client. Each entry must contain a call_id and may override name
  and/or arguments (provided as a dict or JSON string). The proxy applies the
  updates to matching function_call items in the response body and
  re-serializes the response (both non-stream JSON and streaming SSE). Works in
  both synthetic-tool and direct-hook modes.

- RTK integration in example use_tool_hook.py — the example hook script
  now auto-detects the [RTK](https://github.com/rtk-ai/rtk) binary on PATH
  (minimum version 0.23.0+) and rewrites shell commands in tool calls
  (exec_command, shell, Bash) through rtk rewrite, reducing token
  consumption by 60–90%. The check runs on each invocation and gracefully
  skips rewriting if RTK is absent or too old — no configuration change needed.

- 18 new tool-call rewrite tests — TestApplyToolCallUpdates,
  TestReSerializeResponse, and TestHookWithUpdatedToolCalls covering
  argument updates (dict/string), name-only updates, multiple calls,
  non-matching call_id, empty/invalid entries, and end-to-end integration
  with the proxy in both direct-hook and synthetic-tool modes.

- README documentation — added updated_tool_calls to the hook contract
  section, a new RTK integration section with a flow diagram, and updated the
  feature list to mention tool call argument rewriting.

### Fixed

- SSE output item events — _build_sse_from_response now emits
  response.output_item.added and response.output_item.done events for each
  output item (including function_call items) in synthetic SSE streams.
  Previously only response.created and response.completed were emitted,
  preventing clients from parsing individual output items. Added 2 tests to
  verify the new events and that output items can be recovered via
  _response_json_from_sse.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.0.0...v2.1.0](https://github.com/PCODE-pl/MCPTap/compare/v2.0.0...v2.1.0)

## [2.0.0]

### Added

- **`openat2` interception (Linux 5.6+)** — the LD_PRELOAD file-block library
  now intercepts `openat2` via a raw syscall wrapper (glibc has no `openat2`
  wrapper) and exports the `openat2()` symbol for direct-link compatibility.
- **`exec*` interceptors to close the setuid escape vector** — `execve`,
  `execv`, `execvp`, `execvpe`, `posix_spawn`, and `posix_spawnp` are now
  intercepted in the parent process. The library scans `argv` before the child
  is spawned, blocking arguments that resolve to a blocked path. This closes the
  escape vector where `glibc` drops `LD_PRELOAD` for setuid binaries (`sudo`,
  `su`, `doas`, `pkexec`, …), so `sudo cat <blocked>` is refused while
  unrelated `sudo` calls are still allowed.
- **Surgical escalator + interpreter + payload detection** — when `argv[0]` is
  a privilege-escalator and a later argument is an interpreter (`bash`, `sh`,
  `python3`, `perl`, `xargs`, `dd`, …), the concatenated payload is searched
  for a blocklist path as a substring (with `~` expanded anywhere). This blocks
  `sudo bash -c 'cat ~/.fzf-history'` without affecting legitimate uses like
  `sudo bash -c 'systemctl restart nginx'`. Configurable via:
  - `MCPTAP_FB_ESCALATORS` — override the default escalator list
  - `MCPTAP_FB_INTERPRETERS` — override the default interpreter list
  - `MCPTAP_FB_DISABLE_ESCALATOR_CHECK=1` — disable the layer entirely
- **Realpath normalization** — candidate paths and blocklist entries are
  resolved via `realpath`, so `./`, `../`, and symlink aliases can no longer
  bypass the blocklist.
- **Comprehensive file-block test coverage** — 41 new tests covering C-level
  interceptors (`openat`, `openat2`, `open64`, `fopen64`, `statx`, `faccessat`,
  …), Python-level interceptors (`access`, `lstat`, `readlink`, `realpath`,
  `shutil.copy2`), blocklist parsing, dynamic blocklist updates, and
  tool-level blocking (`cp`, `mv`, `dd`, directory blocking).
- **ANSI color output in `setup.sh`** — the "Installation complete" section is
  shown in green; errors (`die()`, unknown options, diagnostic heredocs) are
  shown in red. Colors are gated on TTY detection (no color when piped).
- **`hook=` status in per-request log** — the INFO log line now includes
  `hook=True/False` alongside `intercept=...`.

### Changed

- **Tool-call hook works without MCP intercept config** — the intercept/hook
  loop now runs when either MCP tool interception *or* the tool-call hook
  (`MCP_TAP_USE_TOOL_HOOK`) is enabled. Previously the hook was silently
  disabled when `MCP_TAP_INTERCEPT_YAML` was empty. The two features are
  independent: the hook can gate client tool calls and write `blocked_files`
  for the LD_PRELOAD library even without any MCP intercept config.
- **`--with-file-block` wires both settings** — `wire_file_block_in_proxy_env`
  now enables both `MCP_TAP_USE_TOOL_HOOK` and `MCP_TAP_FILE_BLOCK_LIB` in
  `proxy.env` (previously only the library path was wired). The wiring runs on
  every `--with-file-block` invocation, not only on new installations.
- **Per-session directory path** — changed from `/tmp/mcptap/per_session_id`
  to `/tmp/mcptap/per_session` to match the directory actually created by the
  proxy.
- **File-block library no longer wired through proxy env** — `LD_PRELOAD` is
  applied at command launch (`LD_PRELOAD=… codex …`) rather than through
  `proxy.env`. The example `proxy.env` no longer contains
  `MCP_TAP_FILE_BLOCK_LIB`.
- **Example `use_tool_hook.py` simplified** — the hook now consistently
  returns an `allow` action while preserving the sensitive-file block list,
  instead of reading stdin and applying resource-based blocking.
- **`/home/user/` placeholder substitution** — during installation, `sed`
  replaces all `/home/user/` occurrences in copied example config files with
  the real `$HOME` path.
- **README expanded** — added documentation for file-block limitations
  (stdin/heredoc payloads, obfuscated paths, path-normalization aliases),
  the setuid escape vector, and the escalator/interpreter configuration
  environment variables.

### Fixed

- **Electron/Node.js deadlock** — the generic `syscall()` interceptor
  forwarded all non-`openat2` syscalls through `va_arg` with 6 arguments,
  corrupting argument passing for syscalls with fewer arguments. This caused
  Electron/Node.js (used by `code --locate-extension` in the Codex wrapper) to
  deadlock on `getrandom`/`futex`/thread synchronization. The `syscall()`
  interceptor has been removed entirely; the exported `openat2()` wrapper
  symbol is retained.
- **Test failures on Polish-locale systems** — `LC_ALL=C` is now set in the
  test environment to ensure English error messages.

### Removed

- **Generic `syscall()` interceptor** — removed because it broke
  Electron/Node.js (see Fixed above). Raw `syscall(__NR_openat2, …)` is no
  longer intercepted; programs linking against the `openat2()` wrapper symbol
  are still intercepted.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v1.3.0...v2.0.0](https://github.com/PCODE-pl/MCPTap/compare/v1.3.0...v2.0.0)
