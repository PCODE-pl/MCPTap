# Troubleshooting

## `MCP_TAP_UPSTREAM_PROVIDER must be one of 'openrouter' or 'requesty'`

Check `proxy.env`:

```env
MCP_TAP_UPSTREAM_PROVIDER=openrouter
```

or:

```env
MCP_TAP_UPSTREAM_PROVIDER=requesty
```

## `MCP_TAP_API_KEY must not be empty`

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

## `MCP_TAP_MODEL and MCP_TAP_PLAN_MODE_MODEL must not be empty`

Set both variables in the selected provider file:

```env
MCP_TAP_MODEL=deepseek/deepseek-v4-flash:floor
MCP_TAP_PLAN_MODE_MODEL=z-ai/glm-5.2:floor
```

## Health endpoint does not respond

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

## MCP tool is not resolved

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

## Request fails only in plan mode

Check:

```env
MCP_TAP_PLAN_MODE_TRIGGER=max
MCP_TAP_PLAN_MODE_MAX_INPUT_SIZE=100000
```

If the request input is larger than the configured limit, MCPTap rejects it before forwarding.

## Streaming issues

MCPTap supports streaming SSE responses. For intercepted `/v1/responses` calls, MCPTap may buffer upstream events internally so it can detect hidden function calls, execute MCP tools, and only then return the correct final response to the client.

If you are debugging streaming behavior, enable:

```env
MCP_TAP_LOG_LEVEL=DEBUG
MCP_TAP_LOG_FILE=/tmp/mcptap.log
```
