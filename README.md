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

macOS logs:

```sh
tail -f ~/Library/Logs/mcptap.log ~/Library/Logs/mcptap.error.log
```
