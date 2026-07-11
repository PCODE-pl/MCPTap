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
    {"action": "block", "message": "Instruction for the model"}

This example blocks tool calls when the session has used more than 10000 tokens
or more than 120 seconds, instructing the model to use consult_council instead.
"""

import json
import sys


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
        print(json.dumps({"action": "allow"}))


if __name__ == "__main__":
    main()
