<!-- markdownlint-disable MD024 -->
# Changelog

## [2.10.0]

### Added

- **LLMTR upstream provider** — added the `llmtr` provider, `llmtr.env` template, hot-reload support, setup integration, and documentation for the `https://llmtr.com/v1` OpenAI-compatible gateway.

- **Responses-to-Chat Completions adapter** — added optional `MCP_TAP_USE_CHAT_COMPLETIONS` support for providers and models exposed through `/v1/chat/completions`, preserving function calls, reasoning metadata, conversation history, usage reporting, and Responses-compatible JSON/SSE output.

- **Persistent Chat history** — added SQLite-backed, gzip-compressed `PersistentChatStore` histories with a 15-minute TTL, 200-row limit, 20 MB total limit, 512 KB per-entry limit, and LRU/expiry cleanup. Added 24 tests across `tests/test_chat_completions.py` and `tests/test_chat_store.py`.

### Changed

- **Chat payload size control** — Chat histories are truncated to approximately 120k tokens while preserving system instructions and recent turns, preventing oversized compaction payloads from exceeding upstream context windows.

- **Per-model tool compatibility** — added `disable_custom_tools: true`, which removes unsupported Responses custom tools while preserving function tools. Custom-tool handling now also supports `custom_tool_call` items in Responses parsing and SSE generation.

- **Provider configuration** — generalized provider environment files and upstream URLs, normalized provider names, and moved NanoGPT to `https://nano-gpt.com/api/v1`. The new LLMTR configuration is hot-reloaded together with the other provider settings.

### Fixed

- **Chat streaming compatibility** — completed Responses-compatible SSE conversion for Chat Completions, including response lifecycle events, text and function-call deltas, commentary metadata, logprobs, and final usage, preventing client reconnects and duplicate answers.

- **Chat request normalization** — mapped Responses `developer` messages to the Chat `system` role, omitted empty assistant messages, preserved assistant tool calls, and avoided duplicate instructions in follow-up histories.

- **NanoGPT Muse routing** — routed Muse Spark requests through the Chat transport when required and selected the transport based on the payload model.

- **Adapted response completion** — marked converted assistant answers as final so clients accept them as completed responses.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.9.0...v2.10.0]

## [2.9.0]

### Added

- **NanoGPT upstream provider** — new `nano-gpt.env` template, hot-reload support, and setup docs so NanoGPT can be selected as an upstream provider (`MCP_TAP_UPSTREAM_PROVIDER=nano-gpt`), forwarding to `https://api.nano-gpt.com/api/v1`.

- **Meta upstream provider** — new `meta.env` template, hot-reload support, and setup docs for the Meta Responses-compatible endpoint (`https://api.meta.ai/v1`).

- **Per-model `disable_builtin_tools`** — a per-model option that removes Responses built-in tool types for models that reject them while preserving function tools, kept independent from upstream provider branches. Covered by `test_per_model_disable_builtin_tools_keeps_function_tools_on_every_request` in `tests/test_rewrite.py`.

### Changed

- **Provider config generalization** — provider env files and upstream base URLs are now driven by a shared registry (`_PROVIDER_ENV_FILES`, `_UPSTREAM_BASE_URLS`), and `MCP_TAP_UPSTREAM_PROVIDER` values are normalized to lowercase. Watched env files grow to eight, including `meta.env` and `nano-gpt.env` (covered by `test_nano_gpt_env_is_watched_for_reload` in `tests/test_config_reloader.py`).

- **Meta tool schema normalization** — every tool parameter property is required and optional parameters are encoded as nullable JSON Schema values, applied recursively to nested objects (covered by `test_meta_tool_schemas_require_all_properties_and_nullable_optional_values` and `test_meta_tool_schema_transformation_applies_to_nested_objects` in `tests/test_rewrite.py`).

### Fixed

- Removed model-specific compatibility overrides. Chat and Responses transport
selection is controlled only by the provider setting.

- **Per-model custom tools** — `disable_custom_tools: true` removes unsupported
  Responses custom tools while preserving function tools.

- **Meta unsupported tools** — custom, `tool_search`, `computer_use_preview`, `image_generation`, and `code_interpreter` tool types are dropped (unsupported on the Meta endpoint) and `web_search` is rewritten to `web_search_preview` (covered by `test_meta_drops_unsupported_tool_types_and_rewrites_web_search`).

- **Invalid Meta search metadata** — `search_content_types` is stripped from non-`web_search_preview` tools, preserved only on preview tools (covered by `test_meta_tool_schema_transformation_drops_search_content_types_from_non_preview_tools`).

- **Header redaction setting lookup** — fixed the misspelled `log_fileredact_headers` to `log_file_redact_headers` so upstream request header redaction applies correctly.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.8.1...v2.9.0](https://github.com/PCODE-pl/MCPTap/compare/v2.8.1...v2.9.0)

## [2.8.1]

### Added

- **Credit-checker recovery regression test** — added one test,
  `test_api_failure_does_not_reset_credit_baseline` in
  `tests/test_credits_checker.py`, covering recovery after a failed credits
  API request.

### Fixed

- **False cumulative credit mismatches** — `LogStore.get_last_credit_snapshot`
  now ignores `status="error"` snapshots when selecting the remote usage
  baseline, preventing transient provider API failures from producing large
  false discrepancies after recovery.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.8.0...v2.8.1](https://github.com/PCODE-pl/MCPTap/compare/v2.8.0...v2.8.1)

## [2.8.0]

### Added

- **Encrypted replay tracking** — new `mcptap/encrypted_replay.py` helpers
  fingerprint effective upstream routes, hash model-bound encrypted items, and
  filter invalid reasoning and compaction payloads without exposing encrypted
  content in diagnostics.

- **Encrypted replay test coverage** — added nine tests in
  `tests/test_encrypted_replay.py` across `TestEncryptedReplayFiltering`,
  `TestEncryptedReplayRetry`, `TestResponsesApiParsing`,
  `TestProxyEncryptedReplay`, and `TestEncryptedReplayLogging`.

### Changed

- **Encrypted Responses replay handling** — successful encrypted reasoning and
  compaction items are tracked per session and upstream route. Stale items are
  removed after route changes, and matching upstream 404 rejections trigger a
  single retry with sanitized input for buffered and streamed Responses API
  requests.

- **Streaming error parsing** — streamed Responses API bodies are now parsed
  as JSON when they contain an error response, allowing encrypted replay retry
  detection to work consistently across response modes.

- **Credit snapshot lifecycle** — provider reloads no longer create synthetic
  zero-usage switch snapshots; provider-scoped request logs and valid remote
  snapshots remain the source of credit usage history.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.7.0...v2.8.0](https://github.com/PCODE-pl/MCPTap/compare/v2.7.0...v2.8.0)

## [2.7.0]

### Added

- **Provider credit monitoring** — `CreditsCheckerTask` periodically fetches
  the configured provider credits API, compares remote `total_usage` with
  local request costs, and stores results in the new `credit_snapshots`
  SQLite table. Mismatches and API errors can trigger configurable Telegram
  alerts.

- **Credit-monitoring configuration** — added
  `MCP_TAP_CREDITS_URL`, `MCP_TAP_CREDITS_API_KEY`,
  `MCP_TAP_CREDITS_CHECK_INTERVAL`,
  `MCP_TAP_CREDITS_DISCREPANCY_THRESHOLD`,
  `MCPTAP_TELEGRAM_BOT_TOKEN`, `MCPTAP_TELEGRAM_CHAT_ID`, and
  `MCPTAP_TELEGRAM_ALERT_LEVEL` settings, with OpenRouter and Requesty
  example configuration updates.

- **Expanded file-block interception** — the LD_PRELOAD library now covers
  `stat`, `lstat`, `stat64`, `lstat64`, `fstatat`, `fstatat64`, `opendir`,
  `creat`, `creat64`, `freopen`, and `freopen64` in addition to the existing
  file-access APIs.

### Changed

- **Provider-switch lifecycle** — configuration reloads snapshot the active
  provider's local costs and restart credit monitoring when the provider or
  credits endpoint changes.

- **Request-log storage limits** — request and response bodies larger than
  64 KiB are truncated before being stored in SQLite.

- **Retention and migration safety** — log retention now removes old credit
  snapshots as well as request logs and checkpoints the SQLite WAL; database
  backups are created only when a pending migration exists.

- **Test fixtures updated** — `TestMigrations` and the configuration-reload
  tests now account for the schema version 2 migration and credit-monitoring
  settings.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.6.1...v2.7.0](https://github.com/PCODE-pl/MCPTap/compare/v2.6.1...v2.7.0)

## [2.6.1]

### Added

- **HTTP session-id header injection via LD_PRELOAD** — `file_block.c`
  now intercepts `connect`, `send`, `write`, `close`, `dup`, and `dup2`
  to automatically inject a `session-id: <id>` HTTP header into requests
  sent to the MCPTap proxy on localhost. The header carries
  `CODEX_THREAD_ID` or `HERMES_SESSION_ID` (fallback), so MCPTap can
  attribute requests to the correct session without the client sending
  the header explicitly. The MCPTap listen address is discovered from
  `/proc/<pid>/environ` (PID read from `proxy.pid`, refreshed every 5 s).
  A compact fd bitmap with pthread mutex tracks which sockets connect to
  MCPTap. Only plain HTTP (localhost) is supported — encrypted traffic
  (HTTPS) is not intercepted.

- **`HERMES_SESSION_ID` fallback** — `build_control_path()` in
  `file_block.c` now reads `CODEX_THREAD_ID` first and falls back to
  `HERMES_SESSION_ID` when the former is unset, enabling Hermes Agent
  sessions to use the same per-session file-blocking control files.

- **PID file for LD_PRELOAD discovery** — `mcptap/app.py` writes the
  proxy process PID to `/tmp/mcptap/proxy.pid` at startup and removes it
  on cleanup. The `file_block.c` library reads this file to locate the
  MCPTap process and fetch its listen address from `/proc/<pid>/environ`.

- **`ha()` shell alias** — new alias in `examples/home/user/.bash_aliases`
  launches Hermes Agent with `LD_PRELOAD` file blocking, mirroring the
  existing `cx()` alias for Codex CLI.

- **Shared `get_profile()` helper** — profile detection logic extracted
  from `cx()` into a reusable `get_profile()` function, now used by both
  `cx()` and `ha()`. New project profiles added: `alokai`, `llmcouncil`,
  `shopware`.

- **Hermes Agent documentation** — `README.md` and `docs/FEATURES.md`
  updated with Hermes Agent setup instructions, configuration example
  (`provider: custom`, `base_url`, `api_mode: codex_responses`), and
  references alongside Codex CLI throughout.

- **`docs/ISSUES.md`** — new documentation file listing upstream GitHub
  issues that MCPTap addresses. Organized by project (Hermes Agent, Codex,
  Ollama) with direct links to each issue.

### Changed

- **`cx()` alias simplified** — the `systemctl --user restart mcptap.service`
  call was removed; profile detection now delegates to `get_profile()`.

- **Makefile: link `-lpthread`** — `file_block/Makefile` adds `-lpthread`
  for the pthread mutex used by the fd tracking table and MCPTap address
  cache.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.5.4...v2.6.1](https://github.com/PCODE-pl/MCPTap/compare/v2.5.4...v2.6.1)

## [2.6.0]

### Added

- **HTTP session-id header injection via LD_PRELOAD** — `file_block.c`
  now intercepts `connect`, `send`, `write`, `close`, `dup`, and `dup2`
  to automatically inject a `session-id: <id>` HTTP header into requests
  sent to the MCPTap proxy on localhost. The header carries
  `CODEX_THREAD_ID` or `HERMES_SESSION_ID` (fallback), so MCPTap can
  attribute requests to the correct session without the client sending
  the header explicitly. The MCPTap listen address is discovered from
  `/proc/<pid>/environ` (PID read from `proxy.pid`, refreshed every 5 s).
  A compact fd bitmap with pthread mutex tracks which sockets connect to
  MCPTap. Only plain HTTP (localhost) is supported — encrypted traffic
  (HTTPS) is not intercepted.

- **`HERMES_SESSION_ID` fallback** — `build_control_path()` in
  `file_block.c` now reads `CODEX_THREAD_ID` first and falls back to
  `HERMES_SESSION_ID` when the former is unset, enabling Hermes Agent
  sessions to use the same per-session file-blocking control files.

- **PID file for LD_PRELOAD discovery** — `mcptap/app.py` writes the
  proxy process PID to `/tmp/mcptap/proxy.pid` at startup and removes it
  on cleanup. The `file_block.c` library reads this file to locate the
  MCPTap process and fetch its listen address from `/proc/<pid>/environ`.

- **`ha()` shell alias** — new alias in `examples/home/user/.bash_aliases`
  launches Hermes Agent with `LD_PRELOAD` file blocking, mirroring the
  existing `cx()` alias for Codex CLI.

- **Shared `get_profile()` helper** — profile detection logic extracted
  from `cx()` into a reusable `get_profile()` function, now used by both
  `cx()` and `ha()`. New project profiles added: `alokai`, `llmcouncil`,
  `shopware`.

- **Hermes Agent documentation** — `README.md` and `docs/FEATURES.md`
  updated with Hermes Agent setup instructions, configuration example
  (`provider: custom`, `base_url`, `api_mode: codex_responses`), and
  references alongside Codex CLI throughout.

### Changed

- **`cx()` alias simplified** — the `systemctl --user restart mcptap.service`
  call was removed; profile detection now delegates to `get_profile()`.

- **Makefile: link `-lpthread`** — `file_block/Makefile` adds `-lpthread`
  for the pthread mutex used by the fd tracking table and MCPTap address
  cache.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.5.4...v2.6.0](https://github.com/PCODE-pl/MCPTap/compare/v2.5.4...v2.6.0)

## [2.5.4]

### Added

- GitHub Sponsors funding configuration — new `.github/FUNDING.yml`
  enables the "Sponsor" button on the repository page, linking to
  `pczerkas` and `PCODE-pl` GitHub Sponsors profiles.

### Fixed

- **KeyError on payload rewrite** — `rewrite_json_payload` in
  `mcptap/rewrite.py` unconditionally deleted the `include` key from
  the request payload, raising `KeyError` when the field was absent.
  The removal is now guarded by an `if "include" in payload` check.

### Changed

- Suppress markdownlint MD041 on `LICENSE` — added a
  `<!-- markdownlint-disable-next-line MD041 -->` directive so the
  Apache License heading (which does not start with a Markdown `#`
  heading) no longer triggers the first-line-heading rule.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.5.3...v2.5.4](https://github.com/PCODE-pl/MCPTap/compare/v2.5.3...v2.5.4)

## [2.5.3]

### Added

- SQLite log retention via background asyncio task — a new
  `LogRetentionTask` runs every hour inside the aiohttp event loop and
  deletes log entries older than `MCP_TAP_LOG_RETENTION_DAYS` (default 30).
  The task is wired into the application lifecycle (`on_startup` /
  `on_cleanup`) alongside the existing `ConfigReloader`, and gracefully
  survives transient purge errors (logs and continues).

  - `settings.py` — new field `log_retention_days` (env
    `MCP_TAP_LOG_RETENTION_DAYS`, default `30`).
  - `log_store.py` — new method `LogStore.purge_old(retention_days)`
    that executes `DELETE FROM request_logs WHERE timestamp < ?` and
    returns the number of deleted rows.
  - `log_retention.py` — new module with `LogRetentionTask` class.
  - `app.py` — `_start_log_retention` / `_stop_log_retention` lifecycle
    hooks; skipped when log store is disabled.

  Tests: `tests/test_log_store.py` adds `TestPurgeOld` (4 tests — deletes
  old entries only, returns 0 when nothing to delete / when disabled,
  deletes all when retention is 0). `tests/test_log_retention.py` (5
  tests — purge_once, disabled store, start/stop lifecycle, purge in loop,
  error resilience).

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.5.2...v2.5.3](https://github.com/PCODE-pl/MCPTap/compare/v2.5.2...v2.5.3)

## [2.5.2]

### Added

- Status column in logs UI table — the log viewer now displays the HTTP
  status code as a colored n-tag badge in a dedicated column: green for
  2xx, yellow for 3xx, red for 4xx/5xx. Failed requests (e.g. 429, 404) are
  immediately visible without opening the detail drawer. (mcptap/static/logs.html)

### Fixed

- Error responses not logged — upstream error responses (429, 404, 500)
  were silently dropped from the log database, making them invisible in the
  UI. Three root causes fixed:
  - response_flow.py — record_from_response was called after the
    if status >= 400: break guard in the intercept loop, so error
    responses were forwarded to the client but never persisted. The call is
    now made before the status check.

  - upstream.py — post_upstream_buffered only parsed body_json for
    status < 400, so error bodies were always None. Parsing now runs for
    all status codes, making the full error JSON available for logging.

  - upstream.py / app.py — forward_rewritten (the non-intercept path)
    streamed the response to the client but discarded the body, so
    record_from_response received response_raw=b"". forward_rewritten
    now returns a (StreamResponse, bytes) tuple; app.py passes the
    collected body to the log store.

  New test file tests/test_error_logging.py (5 tests) covers
  TestPostUpstreamBufferedErrorParsing, TestRecordFromResponseErrors, and
  TestInterceptLoopErrorLogging — verifying that 429/500 responses are
  parsed, recorded with correct status codes, and contain the full error
  body.

- Infinite scroll never firing — the logs UI used
  document.querySelector('.n-data-table-base-table') and
  .closest('.n-scrollbar-container') to find the scroll container and
  attach a scroll listener. These internal Naive UI class names are unstable
  and may not exist at mount time, so infinite-scroll loading never fired.
  Replaced with the native @scroll event emitted by n-data-table (where
  e.target is the scroll container with scrollTop/scrollHeight/
  clientHeight). Removed the fragile setupScrollListener/
  nextTick/watch(rows) plumbing. Guarded loadMore to set
  hasMore=false when the API returns 0 rows, preventing infinite retry
  loops. (mcptap/static/logs.html)

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.5.1...v2.5.2](https://github.com/PCODE-pl/MCPTap/compare/v2.5.1...v2.5.2)

## [2.5.1]

### Highlights

- Web-based request log viewer (Vue 3 + Naive UI, dark theme)
- SQLite-backed log store

### Changed

- File-block env vars renamed — the LD_PRELOAD file-blocking library
  environment variables have been renamed from verbose MCPTAP_BLOCKED_*
  names to shorter MCPTAP_FB_* names for consistency and brevity:

    Old name                     New name
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━  ━━━━━━━━━━━━━━━━
    MCPTAP_BLOCKED_DIR           MCPTAP_FB_DIR
  ───────────────────────────  ────────────────
    MCPTAP_BLOCKED_FILES_FILE    MCPTAP_FB_FILE

  The rename touches file_block/file_block.c, tests/test_file_block.py
  (19 references updated across TestLDPreloadLibrary, TestOpenat2Blocking,
  TestDynamicBlocklist, and the _make_fb_env helper), docs/FEATURES.md,
  and setup.sh. All tests pass under the new names.

### Removed

- --profile=mcptap flag — the obsolete --profile=mcptap flag has been
  removed from all Codex CLI startup commands in setup.sh, docs/FEATURES.md,
  and the README example for the file-block library.

### Fixed

- README cleanup — removed duplicated "Requirements" and "Health endpoint"
  sections from the README; the per-session directory description and
  LD_PRELOAD env-var table have been consolidated. A new LD_PRELOAD
  file-block library env-var table (MCPTAP_FB_DIR, MCPTAP_FB_INTERPRETERS,
  MCPTAP_FB_ESCALATORS, MCPTAP_FB_DISABLE_ESCALATOR_CHECK) replaces
  scattered references with a single, structured reference.

### Full Changelog

[https://github.com/PCODE-pl/MCPTap/compare/v2.5.0...v2.5.1](https://github.com/PCODE-pl/MCPTap/compare/v2.5.0...v2.5.1)

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
