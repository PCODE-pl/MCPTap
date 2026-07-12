"""Tests for file access blocking and generic tool hook mode.

Covers:
- LD_PRELOAD library (libmcptap_fileblock.so) integration
- Blocklist file management (write, clear, path generation)
- Generic hook mode (no synthetic tool injection)
- _build_file_block_instruction generation
- _inject_block_instruction into response bodies
- _build_synthetic_tool_response for custom tool names
"""

import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

# Ensure proxy module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy  # noqa: E402

# ---------------------------------------------------------------------------
# Blocklist file management tests
# ---------------------------------------------------------------------------


class TestBlocklistManagement:
    def test_blocklist_file_path_is_deterministic(self):
        with patch.object(proxy, "MCP_TAP_BLOCKLIST_DIR", "/tmp/mcptap_test_blocks"):
            path1 = proxy._blocklist_file_path("session-1")
            path2 = proxy._blocklist_file_path("session-1")
            assert path1 == path2
            assert "blocklist_" in path1
            assert path1.endswith(".txt")

    def test_blocklist_file_path_different_sessions(self):
        with patch.object(proxy, "MCP_TAP_BLOCKLIST_DIR", "/tmp/mcptap_test_blocks"):
            path1 = proxy._blocklist_file_path("session-1")
            path2 = proxy._blocklist_file_path("session-2")
            assert path1 != path2

    def test_write_blocklist_creates_file(self):
        with patch.object(proxy, "MCP_TAP_BLOCKLIST_DIR", tempfile.mkdtemp()):
            files = ["/path/a.py", "/path/b.py", "~/.git-credentials"]
            path = proxy._write_blocklist("s1", files)
            assert os.path.exists(path)
            with open(path) as f:
                lines = f.read().strip().split("\n")
            assert len(lines) == 3
            assert "/path/a.py" in lines
            assert "/path/b.py" in lines
            assert "~/.git-credentials" in lines

    def test_write_blocklist_empty_list(self):
        with patch.object(proxy, "MCP_TAP_BLOCKLIST_DIR", tempfile.mkdtemp()):
            path = proxy._write_blocklist("s1", [])
            assert os.path.exists(path)
            with open(path) as f:
                content = f.read()
            assert content == ""

    def test_clear_blocklist_removes_file(self):
        with patch.object(proxy, "MCP_TAP_BLOCKLIST_DIR", tempfile.mkdtemp()):
            proxy._write_blocklist("s1", ["/some/file"])
            proxy._clear_blocklist("s1")
            assert not os.path.exists(proxy._blocklist_file_path("s1"))

    def test_clear_blocklist_nonexistent_is_noop(self):
        proxy._clear_blocklist("nonexistent-session-id")


# ---------------------------------------------------------------------------
# File block instruction tests
# ---------------------------------------------------------------------------


class TestFileBlockInstruction:
    def test_instruction_empty_when_no_lib(self):
        with patch.object(proxy, "MCP_TAP_FILE_BLOCK_LIB", ""):
            result = proxy._build_file_block_instruction("/tmp/blocklist.txt")
            assert result == ""

    def test_instruction_contains_lib_and_blocklist_path(self):
        with patch.object(proxy, "MCP_TAP_FILE_BLOCK_LIB", "/usr/lib/libmcptap.so"):
            result = proxy._build_file_block_instruction("/tmp/blocklist_abc.txt")
            assert "LD_PRELOAD=/usr/lib/libmcptap.so" in result
            assert "MCPTAP_BLOCKED_FILES_FILE=/tmp/blocklist_abc.txt" in result
            assert "FILE ACCESS BLOCKING" in result


class TestInjectBlockInstruction:
    def test_inject_into_message_with_output_text(self):
        body = {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Running commands..."}],
                }
            ]
        }
        proxy._inject_block_instruction(body, "[BLOCKING ACTIVE]")
        text = body["output"][0]["content"][0]["text"]
        assert "Running commands..." in text
        assert "[BLOCKING ACTIVE]" in text

    def test_inject_creates_new_message_when_no_message(self):
        body = {"output": [{"type": "function_call", "call_id": "c1", "name": "shell"}]}
        proxy._inject_block_instruction(body, "[BLOCKING ACTIVE]")
        # Should append a message item
        messages = [o for o in body["output"] if o.get("type") == "message"]
        assert len(messages) == 1
        assert "[BLOCKING ACTIVE]" in messages[0]["content"][0]["text"]

    def test_inject_empty_output_list(self):
        body = {"output": []}
        proxy._inject_block_instruction(body, "[BLOCKING ACTIVE]")
        assert len(body["output"]) == 1
        assert body["output"][0]["type"] == "message"

    def test_inject_no_output_key(self):
        body = {}
        proxy._inject_block_instruction(body, "[BLOCKING ACTIVE]")
        # Should not crash; output key remains absent
        assert "output" not in body


# ---------------------------------------------------------------------------
# Synthetic tool response tests
# ---------------------------------------------------------------------------


class TestSyntheticToolResponse:
    def test_build_synthetic_tool_response_custom_name(self):
        resp = proxy._build_synthetic_tool_response("model-1", "get_goal")
        assert resp["model"] == "model-1"
        assert resp["status"] == "incompleted"
        assert len(resp["output"]) == 1
        item = resp["output"][0]
        assert item["type"] == "function_call"
        assert item["call_id"] == proxy.SYNTHETIC_GET_GOAL_CALL_ID
        assert item["name"] == "get_goal"

    def test_build_synthetic_tool_response_different_name(self):
        resp = proxy._build_synthetic_tool_response("model-1", "custom_tool")
        item = resp["output"][0]
        assert item["name"] == "custom_tool"
        assert item["call_id"] == proxy.SYNTHETIC_GET_GOAL_CALL_ID

    def test_build_synthetic_get_goal_response_still_works(self):
        resp = proxy._build_synthetic_get_goal_response("model-1")
        item = resp["output"][0]
        assert item["name"] == proxy.SYNTHETIC_GET_GOAL_TOOL_NAME


# ---------------------------------------------------------------------------
# Generic hook mode (no synthetic tool) tests
# ---------------------------------------------------------------------------


def make_hook_script_with_blocked_files(blocked_files: list) -> str:
    """Create a hook script that returns allow with blocked_files."""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".py")
    os.close(fd)
    files_json = json.dumps(blocked_files)
    with open(path, "w") as f:
        f.write(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "data = json.load(sys.stdin)\n"
            f"print(json.dumps({{'action': 'allow', 'blocked_files': {files_json}}}))\n"
        )
    os.chmod(path, 0o755)
    return path


class TestGenericHookMode:
    def test_synthetic_tool_config_default(self):
        """Default MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL is 'get_goal'."""
        # This is set from env at import time; just verify the constant exists
        assert hasattr(proxy, "MCP_TAP_USE_TOOL_HOOK_SYNTHETIC_TOOL")

    @pytest.mark.asyncio
    async def test_hook_returns_blocked_files_in_allow(self):
        """Hook can return blocked_files in the allow response."""
        hook_path = make_hook_script_with_blocked_files(["/secret/file.py"])
        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)
                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=b"",
                        saved_body_json={},
                        client_tool_calls=[{"call_id": "c1", "name": "shell", "arguments": {}}],
                        get_goal_result={},
                        forced_model="m",
                        used_tokens=0,
                        used_time_seconds=0.0,
                    )
                    result = await gw.run_hook(state)
                    assert result["action"] == "allow"
                    assert "blocked_files" in result
                    assert "/secret/file.py" in result["blocked_files"]
        finally:
            os.unlink(hook_path)

    @pytest.mark.asyncio
    async def test_hook_without_blocked_files_still_works(self):
        """Hook returning allow without blocked_files works as before."""
        import tempfile

        fd, hook_path = tempfile.mkstemp(suffix=".py")
        os.close(fd)
        with open(hook_path, "w") as f:
            f.write("#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps({'action': 'allow'}))\n")
        os.chmod(hook_path, 0o755)

        try:
            with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK", hook_path):
                with patch.object(proxy, "MCP_TAP_USE_TOOL_HOOK_TIMEOUT", 10.0):
                    tracker = proxy.SessionTracker()
                    gw = proxy.ToolHookGateway(tracker)
                    state = proxy.PendingState(
                        session_id="s1",
                        saved_status=200,
                        saved_headers={},
                        saved_raw=b"",
                        saved_body_json={},
                        client_tool_calls=[],
                        get_goal_result={},
                        forced_model="m",
                        used_tokens=0,
                        used_time_seconds=0.0,
                    )
                    result = await gw.run_hook(state)
                    assert result["action"] == "allow"
                    assert result.get("blocked_files", []) == []
        finally:
            os.unlink(hook_path)


# ---------------------------------------------------------------------------
# LD_PRELOAD library integration tests
# ---------------------------------------------------------------------------


LIB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..",
    "mcp-tap-extras",
    "file_block",
    "libmcptap_fileblock.so",
)
LIB_PATH = os.path.normpath(LIB_PATH)


def lib_exists():
    return os.path.isfile(LIB_PATH)


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestLDPreloadLibrary:
    def test_blocked_file_cannot_be_read(self, tmp_path):
        """A file in the blocklist cannot be opened with LD_PRELOAD."""
        import subprocess

        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)

        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_non_blocked_file_can_be_read(self, tmp_path):
        """Files not in the blocklist are still readable."""
        import subprocess

        ok_file = tmp_path / "ok.txt"
        ok_file.write_text("ok content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("/tmp/nonexistent.txt\n")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)

        result = subprocess.run(
            ["cat", str(ok_file)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "ok content" in result.stdout

    def test_tilde_expansion_in_blocklist(self, tmp_path):
        """Tilde in blocklist is expanded to HOME."""
        import subprocess

        # Use a real file under HOME for testing
        home = os.path.expanduser("~")
        test_file = os.path.join(home, ".mcptap_test_block_file")
        with open(test_file, "w") as f:
            f.write("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("~/.mcptap_test_block_file\n")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)

        try:
            result = subprocess.run(
                ["cat", test_file],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode != 0
            assert "Permission denied" in result.stderr
        finally:
            os.unlink(test_file)

    def test_empty_blocklist_allows_all(self, tmp_path):
        """An empty blocklist does not block anything."""
        import subprocess

        ok_file = tmp_path / "ok.txt"
        ok_file.write_text("ok content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)

        result = subprocess.run(
            ["cat", str(ok_file)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "ok content" in result.stdout

    def test_multiple_files_blocked(self, tmp_path):
        """Multiple files in the blocklist are all blocked."""
        import subprocess

        f1 = tmp_path / "f1.txt"
        f1.write_text("secret1")
        f2 = tmp_path / "f2.txt"
        f2.write_text("secret2")
        f3 = tmp_path / "f3.txt"
        f3.write_text("ok")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(f"{f1}\n{f2}\n")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)

        # f1 blocked
        result = subprocess.run(["cat", str(f1)], capture_output=True, text=True, env=env)
        assert result.returncode != 0

        # f2 blocked
        result = subprocess.run(["cat", str(f2)], capture_output=True, text=True, env=env)
        assert result.returncode != 0

        # f3 not blocked
        result = subprocess.run(["cat", str(f3)], capture_output=True, text=True, env=env)
        assert result.returncode == 0

    def test_dynamic_blocklist_reload(self, tmp_path):
        """Blocklist is reloaded when the control file changes."""
        import subprocess

        target = tmp_path / "dynamic.txt"
        target.write_text("dynamic content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("")  # Empty initially

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)

        # First read: not blocked
        result = subprocess.run(["cat", str(target)], capture_output=True, text=True, env=env)
        assert result.returncode == 0

        # Add file to blocklist
        blocklist.write_text(str(target) + "\n")

        # Second read: blocked (within 1 second reload interval)
        result = subprocess.run(["cat", str(target)], capture_output=True, text=True, env=env)
        assert result.returncode != 0

    def test_python_open_blocked(self, tmp_path):
        """Python's open() is also blocked by LD_PRELOAD."""
        import subprocess

        blocked = tmp_path / "secret.py"
        blocked.write_text("secret = 42")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)

        result = subprocess.run(
            ["python3", "-c", f"f = open('{blocked}'); print(f.read())"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_stat_blocked(self, tmp_path):
        """os.stat() is also blocked by LD_PRELOAD."""
        import subprocess

        blocked = tmp_path / "stat_test.txt"
        blocked.write_text("content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)

        result = subprocess.run(
            ["python3", "-c", f"import os; os.stat('{blocked}'); print('OK')"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr
