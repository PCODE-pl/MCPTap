<!-- markdownlint-disable MD024 -->
# Changelog

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
