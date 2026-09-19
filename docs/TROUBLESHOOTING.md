# Troubleshooting

## `MCPTAP_UPSTREAM_PROVIDER must be one of 'openrouter' or 'requesty'`

Check `proxy.env`:

```env
MCPTAP_UPSTREAM_PROVIDER=openrouter
```

or:

```env
MCPTAP_UPSTREAM_PROVIDER=requesty
```

## `MCPTAP_API_KEY must not be empty`

Set your API key in the selected provider file:

```sh
~/.config/mcptap/openrouter.env
```

or:

```sh
~/.config/mcptap/requesty.env
```

or:

```sh
~/.config/mcptap/meta.env
```

Example:

```env
MCPTAP_API_KEY=sk-or-v1-...
```

## `MCPTAP_MODEL and MCPTAP_PLAN_MODE_MODEL must not be empty`

Set both variables in the selected provider file:

```env
MCPTAP_MODEL=deepseek/deepseek-v4-flash:floor
MCPTAP_PLAN_MODE_MODEL=z-ai/glm-5.2:floor
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
MCPTAP_LISTEN_HOST=127.0.0.1
MCPTAP_LISTEN_PORT=8787
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
MCPTAP_PLAN_MODE_TRIGGER=max
MCPTAP_PLAN_MODE_MAX_INPUT_SIZE=100000
```

If the request input is larger than the configured limit, MCPTap rejects it before forwarding.

## Streaming issues

MCPTap supports streaming SSE responses. For intercepted `/v1/responses` calls, MCPTap may buffer upstream events internally so it can detect hidden function calls, execute MCP tools, and only then return the correct final response to the client.

If you are debugging streaming behavior, enable:

```env
MCPTAP_LOG_LEVEL=DEBUG
MCPTAP_LOG_FILE=/tmp/mcptap.log
```
