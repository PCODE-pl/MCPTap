#!/usr/bin/env python3
"""Example use_tool_hook script for MCPTap.

MCPTap calls this script before allowing the model's client tool calls to
execute. The script receives a JSON object on stdin and must print a JSON
decision on stdout.

Input (stdin):
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

Output (stdout):
    {"action": "allow"}
    or
    {"action": "allow", "blocked_files": ["/path/to/secret.py", "~/.git-credentials"]}
    or
    {"action": "block", "message": "Instruction for the model"}

When ``blocked_files`` is present in an ``allow`` response and
``MCP_TAP_FILE_BLOCK_LIB`` is configured, MCPTap writes the list to a
control file and injects an instruction telling the model to prefix all
shell commands with ``LD_PRELOAD=<lib> MCPTAP_BLOCKED_FILES_FILE=<path>``.

This example:
- Blocks tool calls when the session exceeds 10000 tokens or 120 seconds.
- Always blocks access to sensitive files when tool calls are allowed.
"""

import json
import sys

# Files that should never be accessible by the model's tool calls.
SENSITIVE_FILES = [
    "~/.git-credentials",
    "~/.ssh/id_rsa",
    "~/.ssh/id_ed25519",
]


def main() -> None:
    data = json.load(sys.stdin)

    used_tokens = data.get("used_tokens", 0)
    used_time = data.get("used_time_seconds", 0.0)

    if used_tokens > 10000 or used_time > 120:
        print(
            json.dumps(
                {
                    "action": "block",
                    "message": (
                        "You have used significant resources this session "
                        f"({used_tokens} tokens, {used_time:.0f}s). "
                        "Use the consult_council tool to review your approach "
                        "before making more tool calls."
                    ),
                }
            )
        )
    else:
        print(
            json.dumps(
                {
                    "action": "allow",
                    "blocked_files": SENSITIVE_FILES,
                }
            )
        )


if __name__ == "__main__":
    main()
