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
    {"action": "allow", "updated_tool_calls": [
        {"call_id": "...", "name": "exec_command", "arguments": {"cmd": "rtk git status"}}
    ]}
    or
    {"action": "block", "message": "Instruction for the model"}

When ``blocked_files`` is present in an ``allow`` response, MCPTap writes
the list to a control file.

When ``updated_tool_calls`` is present in an ``allow`` response, MCPTap
rewrites the matching tool call arguments in the response before returning
it to the client.  Each entry must contain a ``call_id`` and may override
``name`` and/or ``arguments``.  This is useful for transparently wrapping
shell commands with a token-compression tool such as RTK.

This example:
- Blocks tool calls when the session exceeds 10000 tokens or 120 seconds.
- Always blocks access to sensitive files when tool calls are allowed.
- Rewrites exec_command shell calls through RTK when available.
"""

import json
import shutil
import subprocess
import sys

# Files that should never be accessible by the model's tool calls.
SENSITIVE_FILES = [
    "~/.git-credentials",
    "~/.gitconfig",
    "~/.ssh/id_rsa",
    "~/.ssh/id_ed25519",
]

# Tool names whose arguments contain a shell command that can be rewritten.
RTK_TOOL_NAMES = {"exec_command", "shell", "Bash"}
RTK_CMD_FIELDS = ["cmd", "command"]

# Minimum rtk version required for ``rtk rewrite``.
RTK_MIN_VERSION = (0, 23, 0)


def _rtk_available() -> bool:
    """Return True if rtk binary is on PATH and meets the minimum version."""
    rtk_bin = shutil.which("rtk")
    if not rtk_bin:
        return False
    try:
        result = subprocess.run([rtk_bin, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False
        # Output looks like "rtk 0.28.2"
        parts = result.stdout.strip().split()
        if len(parts) < 2:
            return False
        ver_parts = parts[1].split(".")
        if len(ver_parts) < 3:
            return False
        version = tuple(int(x) for x in ver_parts[:3])
        return version >= RTK_MIN_VERSION
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return False


def _rtk_rewrite(command: str, rtk_bin: str) -> str | None:
    """Call ``rtk rewrite <command>`` and return the rewritten command.

    Returns ``None`` if rtk decides not to rewrite (no match) or on error.
    """
    try:
        result = subprocess.run(
            [rtk_bin, "rewrite", command],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Exit 0 = rewritten, exit 3 = no match, other = error
        if result.returncode not in (0, 3):
            return None
        rewritten = result.stdout.strip()
        if not rewritten or rewritten == command:
            return None
        return rewritten
    except (OSError, subprocess.TimeoutExpired):
        return None


def _rewrite_tool_calls(
    tool_calls: list[dict],
    rtk_bin: str,
) -> list[dict]:
    """Return updated_tool_calls entries for shell commands that rtk can compress."""
    updates = []
    for tc in tool_calls:
        name = tc.get("name", "")
        if name not in RTK_TOOL_NAMES:
            continue
        arguments = tc.get("arguments", {})
        if not isinstance(arguments, dict):
            continue
        call_id = tc.get("call_id")
        if not call_id:
            continue
        # Find the command field
        cmd = None
        for field in RTK_CMD_FIELDS:
            if field in arguments:
                cmd = arguments[field]
                break
        if not cmd or not isinstance(cmd, str):
            continue
        rewritten = _rtk_rewrite(cmd, rtk_bin)
        if rewritten:
            new_args = dict(arguments)
            for field in RTK_CMD_FIELDS:
                if field in new_args:
                    new_args[field] = rewritten
                    break
            updates.append(
                {
                    "call_id": call_id,
                    "name": name,
                    "arguments": new_args,
                }
            )
    return updates


def main() -> None:
    data = json.load(sys.stdin)

    # used_tokens = data.get("used_tokens", 0)
    # used_time = data.get("used_time_seconds", 0.0)
    # if used_tokens > 10000 or used_time > 120:
    #     print(
    #         json.dumps(
    #             {
    #                 "action": "block",
    #                 "message": (
    #                     "You have used significant resources this session "
    #                     f"({used_tokens} tokens, {used_time:.0f}s). "
    #                     "Use the consult_council tool to review your approach "
    #                     "before making more tool calls."
    #                 ),
    #             }
    #         )
    #     )
    #     return

    tool_calls = data.get("tool_calls", [])

    response: dict = {"action": "allow", "blocked_files": SENSITIVE_FILES}

    # Attempt RTK rewriting of shell commands
    rtk_bin = shutil.which("rtk")
    if rtk_bin and _rtk_available():
        updates = _rewrite_tool_calls(tool_calls, rtk_bin)
        if updates:
            response["updated_tool_calls"] = updates

    print(json.dumps(response))


if __name__ == "__main__":
    main()
