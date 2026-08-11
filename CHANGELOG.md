<!-- markdownlint-disable MD024 -->
# Changelog

## [2.7.0]

### Added
- **Provider credit monitoring** — `mcptap/credits_checker.py` now polls provider credits API and compares remote `total_usage` against the sum of costs from `request_logs`. Discrepancies are detected and can trigger Telegram alerts via `MCPTAP_TELEGRAM_BOT_TOKEN`, `MCPTAP_TELEGRAM_CHAT_ID`, and `MCPTAP_TELEGRAM_ALERT_LEVEL` settings. Provider snapshot states are persisted in `credit_snapshots` table for migration tracking.
- **Expanded file-block interception** — `file_block.c` now covers additional file and directory APIs beyond the original libc syscalls (`open`, `openat`, `fopen`, `stat`, `access`, `readlink`, `realpath`, `openat2`). The library intercepts more file operation paths to strengthen blocking coverage.
- **`HERMES_SESSION_ID` fallback in control path** — `build_control_path()` in `file_block.c` now reads `CODEX_THREAD_ID` first and falls back to `HERMES_SESSION_ID`, enabling Hermes Agent sessions to use the same per-session file-blocking control files.
- **Credit settings in example configs** — Example config files (`examples/home/user/.config/mcptap/openrouter.env`, `proxy.env`, `requesty.env`) updated with `MCP_TAP_CREDITS_URL`, `MCP_TAP_CREDITS_API_KEY`, `MCP_TAP_CREDITS_CHECK_INTERVAL`, and `MCP_TAP_CREDITS_DISCREPANCY_THRESHOLD` environment variables.

### Changed
- **PID file for LD_PRELOAD discovery** — `mcptap/app.py` writes the proxy process PID to `/tmp/mcptap/proxy.pid` at startup and removes it on cleanup. The `file_block.c` library reads this file to locate the MCPTap process and fetch its listen address from `/proc/<pid>/environ`.
- **Shared `get_profile()` helper** — Profile detection logic extracted from `cx()` into a reusable `get_profile()` function, now used by both `cx()` and `ha()`. New project profiles added: `alokai`, `llmcouncil`, `shopware`.

### Fixed
- **KeyError on payload rewrite** — `rewrite_json_payload` in `mcptap/rewrite.py` now guards removal of the `include` key with an `if "include" in payload` check, preventing errors when the field is absent.

### Full Changelog
[https://github.com/PCODE-pl/MCPTap/compare/v2.6.1...v2.7.0](https://github.com/PCODE-pl/MCPTap/compare/v2.6.1...v2.7.0)