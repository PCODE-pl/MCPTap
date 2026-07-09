# MCPTap

## Installation

MCPTap requires Python 3.10 or newer.

Install the latest release:

```sh
curl -fsSL https://raw.githubusercontent.com/PCODE-pl/MCPTap/master/setup.sh | sh
```

If `curl` is not available:

```sh
wget -qO- https://raw.githubusercontent.com/PCODE-pl/MCPTap/master/setup.sh | sh
```

Edit the configuration files after installation:

```sh
~/.config/mcptap/proxy.env
~/.config/mcptap/openrouter.env
~/.config/mcptap/requesty.env
```

Check the service:

```sh
curl http://127.0.0.1:8787/health
```

Linux logs:

```sh
journalctl --user -u mcptap.service -f
```

```sh
journalctl --user -u mcptap.service -p debug -f
```

macOS logs:

```sh
tail -f ~/Library/Logs/mcptap.log ~/Library/Logs/mcptap.error.log
```

## Configuration

### Codex

```toml
model_provider = "mcptap"
model = "openai/gpt-5.5"
model_context_window = 1000000

# cannot equal to MCP_TAP_PLAN_MODE_TRIGGER (defaults to: "max").
# For this reasoning effort value MCPTap will use MCP_TAP_MODEL
# from provider env file
model_reasoning_effort = "xhigh"

# has to equal to MCP_TAP_PLAN_MODE_TRIGGER (defaults to: "max").
# For this reasoning effort value MCPTap will use MCP_TAP_PLAN_MODE_MODEL
# from provider env file
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
