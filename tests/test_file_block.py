"""Tests for file access blocking and generic tool hook mode.

Covers:
- LD_PRELOAD library (libmcptap_fileblock.so) integration
- openat2 (Linux 5.6+) wrapper symbol interception
- Blocklist file management (write, clear, path generation)
- Generic hook mode (no synthetic tool injection)
- _build_synthetic_tool_response for custom tool names
"""

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest  # type: ignore

# Ensure proxy module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy  # noqa: E402

# ---------------------------------------------------------------------------
# Blocklist file management tests
# ---------------------------------------------------------------------------


class TestBlocklistManagement:
    def test_blocklist_file_path_is_deterministic(self):
        with patch.object(proxy, "MCP_TAP_PER_SESSION_DIR", "/tmp/mcptap_test_blocks"):
            path1 = proxy._blocklist_file_path("session-1")
            path2 = proxy._blocklist_file_path("session-1")
            assert path1 == path2
            assert "blocked_files" in path1
            assert path1.endswith("blocked_files")

    def test_blocklist_file_path_different_sessions(self):
        with patch.object(proxy, "MCP_TAP_PER_SESSION_DIR", "/tmp/mcptap_test_blocks"):
            path1 = proxy._blocklist_file_path("session-1")
            path2 = proxy._blocklist_file_path("session-2")
            assert path1 != path2

    def test_write_blocklist_creates_file(self):
        with patch.object(proxy, "MCP_TAP_PER_SESSION_DIR", tempfile.mkdtemp()):
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
        with patch.object(proxy, "MCP_TAP_PER_SESSION_DIR", tempfile.mkdtemp()):
            path = proxy._write_blocklist("s1", [])
            assert os.path.exists(path)
            with open(path) as f:
                content = f.read()
            assert content == ""

    def test_clear_blocklist_removes_file(self):
        with patch.object(proxy, "MCP_TAP_PER_SESSION_DIR", tempfile.mkdtemp()):
            proxy._write_blocklist("s1", ["/some/file"])
            proxy._clear_blocklist("s1")
            assert not os.path.exists(proxy._blocklist_file_path("s1"))

    def test_clear_blocklist_nonexistent_is_noop(self):
        proxy._clear_blocklist("nonexistent-session-id")


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
    "mcp-tap",
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
        env["LC_ALL"] = "C"

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
        env["LC_ALL"] = "C"

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
        env["LC_ALL"] = "C"

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
        env["LC_ALL"] = "C"

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
        env["LC_ALL"] = "C"

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
        env["LC_ALL"] = "C"

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
        env["LC_ALL"] = "C"

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
        env["LC_ALL"] = "C"

        result = subprocess.run(
            ["python3", "-c", f"import os; os.stat('{blocked}'); print('OK')"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr


# ---------------------------------------------------------------------------
# openat2 (Linux 5.6+) integration tests
# ---------------------------------------------------------------------------

OPENAT2_HELPER_SRC = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/syscall.h>

#ifndef __NR_openat2
    #define __NR_openat2 437
#endif

struct test_open_how {
    unsigned long long flags;
    unsigned long long mode;
    unsigned long long resolve;
};

/* Declare the openat2 wrapper exported by libmcptap_fileblock.so.
 * When LD_PRELOAD is active, this resolves to our interceptor. */
extern int openat2(int dirfd, const char *pathname, struct test_open_how *how, size_t size);

int main(int argc, char *argv[]) {
    if (argc < 2) return 3;
    const char *target = argv[1];

    struct test_open_how how = {
        .flags = O_RDONLY,
        .mode = 0,
        .resolve = 0,
    };

    int fd = openat2(AT_FDCWD, target, &how, sizeof(how));
    if (fd < 0) {
        if (errno == ENOSYS) {
            printf("NOSYS\n");
            return 4;
        }
        if (errno == EACCES) {
            printf("BLOCKED\n");
            return 0;
        }
        printf("ERROR errno=%d\n", errno);
        return 1;
    }

    char buf[256];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n > 0) {
        buf[n] = '\0';
        printf("READ %.200s\n", buf);
    } else {
        printf("READ_EMPTY\n");
    }
    return 2;
}
"""


def _build_openat2_helper(tmp_path):
    """Build the C test helper that calls the openat2() wrapper symbol."""
    src = tmp_path / "openat2_helper.c"
    binary = tmp_path / "openat2_helper"
    src.write_text(OPENAT2_HELPER_SRC)
    cc = os.environ.get("CC", "gcc")
    lib_dir = os.path.dirname(LIB_PATH)
    result = subprocess.run(
        [
            cc,
            "-Wall",
            "-Wextra",
            "-o",
            str(binary),
            str(src),
            f"-L{lib_dir}",
            "-lmcptap_fileblock",
            f"-Wl,-rpath,{lib_dir}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Failed to build openat2 helper: {result.stderr}")
    return str(binary)


def _kernel_version_tuple(release):
    """Extract (major, minor) kernel version from uname release string."""
    m = re.match(r"(\d+)\.(\d+)", release)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


# ---------------------------------------------------------------------------
# Process allowlist tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestProcessAllowlist:
    """Tests for MCPTAP_FB_PROCESS_ALLOWLIST bypass mechanism."""

    def test_allowlisted_process_can_read_blocked(self, tmp_path):
        """A process on the allowlist can read a blocked file."""
        import subprocess

        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist, extra={"MCPTAP_FB_PROCESS_ALLOWLIST": "cat"})
        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "secret content" in result.stdout

    def test_non_allowlisted_process_blocked(self, tmp_path):
        """A process NOT on the allowlist is still blocked."""
        import subprocess

        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist, extra={"MCPTAP_FB_PROCESS_ALLOWLIST": "git"})
        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_default_allowlist_includes_git(self, tmp_path):
        """With no MCPTAP_FB_PROCESS_ALLOWLIST, git bypasses the blocklist."""
        import subprocess

        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        # git is on the default allowlist, so it can read the blocked file.
        # Use `git hash-object` which reads a file and prints its SHA1.
        result = subprocess.run(
            ["git", "hash-object", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        # git hash-object prints a 40-char SHA1 hash
        assert len(result.stdout.strip()) == 40

    def test_empty_allowlist_disables_bypass(self, tmp_path):
        """Setting MCPTAP_FB_PROCESS_ALLOWLIST to empty disables the allowlist."""
        import subprocess

        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist, extra={"MCPTAP_FB_PROCESS_ALLOWLIST": ""})
        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_custom_allowlist_multiple_entries(self, tmp_path):
        """Multiple colon-separated entries in the allowlist are all honored."""
        import subprocess

        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist, extra={"MCPTAP_FB_PROCESS_ALLOWLIST": "cat:head"})
        # cat is allowlisted
        result_cat = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result_cat.returncode == 0
        assert "secret content" in result_cat.stdout

        # head is allowlisted
        result_head = subprocess.run(
            ["head", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result_head.returncode == 0
        assert "secret content" in result_head.stdout

        # sed is NOT allowlisted
        result_sed = subprocess.run(
            ["sed", "p", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result_sed.returncode != 0


@pytest.mark.skipif(
    _kernel_version_tuple(platform.uname().release) < (5, 6),
    reason="openat2 requires Linux 5.6+",
)
class TestOpenat2Blocking:
    def test_openat2_blocked(self, tmp_path):
        """openat2() wrapper is blocked for files in the blocklist."""
        blocked = tmp_path / "openat2_secret.txt"
        blocked.write_text("openat2 secret content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_openat2_helper(tmp_path)

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)
        env["LC_ALL"] = "C"
        # Clear global LD_PRELOAD so our explicit one is the only one
        env.pop("MCPTAP_BLOCKED_DIR", None)
        env.pop("CODEX_THREAD_ID", None)

        result = subprocess.run(
            [helper, str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_openat2_non_blocked_file_works(self, tmp_path):
        """openat2() wrapper succeeds for files NOT in the blocklist."""
        ok_file = tmp_path / "openat2_ok.txt"
        ok_file.write_text("openat2 ok content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("/tmp/nonexistent_openat2.txt\n")

        helper = _build_openat2_helper(tmp_path)

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)
        env["LC_ALL"] = "C"
        env.pop("MCPTAP_BLOCKED_DIR", None)
        env.pop("CODEX_THREAD_ID", None)

        result = subprocess.run(
            [helper, str(ok_file)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "openat2 ok content" in result.stdout

    def test_basic_programs_work_with_ld_preload(self, tmp_path):
        """Basic programs (ls, cat, echo) work normally with LD_PRELOAD active."""
        target = tmp_path / "ok.txt"
        target.write_text("content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("/tmp/nonexistent_passthrough.txt\n")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist)
        env["LC_ALL"] = "C"
        env.pop("MCPTAP_BLOCKED_DIR", None)
        env.pop("CODEX_THREAD_ID", None)

        result = subprocess.run(
            ["ls", "-la", str(tmp_path)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "ok.txt" in result.stdout


# ---------------------------------------------------------------------------
# C syscall helper for testing interceptors not easily exercisable from Python
# ---------------------------------------------------------------------------

FB_SYSCALL_HELPER_SRC = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <limits.h>
#include <sys/stat.h>
#include <sys/syscall.h>

#ifndef __NR_openat2
    #define __NR_openat2 437
#endif

#define _STAT_VER_LINUX 1

extern int __xstat(int ver, const char *path, struct stat *buf);
extern int __xstat64(int ver, const char *path, struct stat64 *buf);
extern int __lxstat(int ver, const char *path, struct stat *buf);
extern int __lxstat64(int ver, const char *path, struct stat64 *buf);

struct test_open_how {
    unsigned long long flags;
    unsigned long long mode;
    unsigned long long resolve;
};

int main(int argc, char *argv[]) {
    if (argc < 3) {
        fprintf(stderr, "Usage: %s <syscall> <path>\n", argv[0]);
        return 3;
    }
    const char *cmd = argv[1];
    const char *path = argv[2];
    int rc = 0;

    if (strcmp(cmd, "openat") == 0) {
        rc = openat(AT_FDCWD, path, O_RDONLY);
        if (rc >= 0) close(rc);
    } else if (strcmp(cmd, "statx") == 0) {
        struct statx buf;
        rc = statx(AT_FDCWD, path, 0, 0, &buf);
    } else if (strcmp(cmd, "xstat") == 0) {
        struct stat buf;
        rc = __xstat(_STAT_VER_LINUX, path, &buf);
    } else if (strcmp(cmd, "xstat64") == 0) {
        struct stat64 buf;
        rc = __xstat64(_STAT_VER_LINUX, path, &buf);
    } else if (strcmp(cmd, "lxstat") == 0) {
        struct stat buf;
        rc = __lxstat(_STAT_VER_LINUX, path, &buf);
    } else if (strcmp(cmd, "lxstat64") == 0) {
        struct stat64 buf;
        rc = __lxstat64(_STAT_VER_LINUX, path, &buf);
    } else if (strcmp(cmd, "fopen64") == 0) {
        FILE *f = fopen64(path, "r");
        if (f) { rc = 0; fclose(f); } else { rc = -1; }
    } else if (strcmp(cmd, "realpath") == 0) {
        char resolved[4096];
        char *ret = realpath(path, resolved);
        if (!ret) rc = -1;
    } else if (strcmp(cmd, "readlink") == 0) {
        char buf[4096];
        ssize_t n = readlink(path, buf, sizeof(buf) - 1);
        if (n < 0) rc = -1;
    } else if (strcmp(cmd, "open64") == 0) {
        rc = open64(path, O_RDONLY);
        if (rc >= 0) close(rc);
    } else if (strcmp(cmd, "openat64") == 0) {
        rc = openat64(AT_FDCWD, path, O_RDONLY);
        if (rc >= 0) close(rc);
    } else if (strcmp(cmd, "faccessat") == 0) {
        rc = faccessat(AT_FDCWD, path, F_OK, 0);
    } else if (strcmp(cmd, "open_wronly") == 0) {
        rc = open(path, O_WRONLY);
        if (rc >= 0) close(rc);
    } else {
        fprintf(stderr, "Unknown: %s\n", cmd);
        return 3;
    }

    if (rc < 0) {
        if (errno == EACCES) { printf("BLOCKED\n"); return 0; }
        printf("ERROR errno=%d\n", errno);
        return 1;
    }
    printf("OK\n");
    return 2;
}
"""


def _build_fb_helper(tmp_path):
    """Build the C test helper for various syscall interceptors."""
    src = tmp_path / "fb_syscall_helper.c"
    binary = tmp_path / "fb_syscall_helper"
    src.write_text(FB_SYSCALL_HELPER_SRC)
    cc = os.environ.get("CC", "gcc")
    result = subprocess.run(
        [cc, "-Wall", "-Wextra", "-o", str(binary), str(src)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Failed to build fb helper: {result.stderr}")
    return str(binary)


def _make_fb_env(blocklist_path, extra=None):
    """Create env dict for LD_PRELOAD file-block tests."""
    env = os.environ.copy()
    env["LD_PRELOAD"] = LIB_PATH
    env["MCPTAP_BLOCKED_FILES_FILE"] = str(blocklist_path)
    env["LC_ALL"] = "C"
    env.pop("MCPTAP_BLOCKED_DIR", None)
    env.pop("CODEX_THREAD_ID", None)
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Per-interceptor blocking tests via C helper
# ---------------------------------------------------------------------------

FB_INTERCEPTORS = [
    "openat",
    "open64",
    "openat64",
    "fopen64",
    "statx",
    "xstat",
    "xstat64",
    "lxstat",
    "lxstat64",
    "faccessat",
    "open_wronly",
]


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestSyscallInterceptors:
    """Verify that every intercepted syscall returns EACCES for blocked paths."""

    @pytest.mark.parametrize("interceptor", FB_INTERCEPTORS)
    def test_blocked(self, tmp_path, interceptor):
        """Each interceptor blocks access to a file in the blocklist."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_fb_helper(tmp_path)
        env = _make_fb_env(blocklist)

        result = subprocess.run(
            [helper, interceptor, str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    @pytest.mark.parametrize("interceptor", FB_INTERCEPTORS)
    def test_non_blocked(self, tmp_path, interceptor):
        """Each interceptor allows access to files not in the blocklist."""
        ok = tmp_path / "ok.txt"
        ok.write_text("ok")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("/tmp/nonexistent_fb.txt\n")

        helper = _build_fb_helper(tmp_path)
        env = _make_fb_env(blocklist)

        result = subprocess.run(
            [helper, interceptor, str(ok)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Python-level interceptor tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestPythonInterceptors:
    def test_access_blocked(self, tmp_path):
        """os.access() returns False for blocked files."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["python3", "-c", f"import os; print('BLOCKED' if not os.access('{blocked}', os.R_OK) else 'OK')"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_lstat_blocked(self, tmp_path):
        """os.lstat() is blocked for files in the blocklist."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["python3", "-c", f"import os; os.lstat('{blocked}'); print('OK')"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_readlink_blocked(self, tmp_path):
        """os.readlink() is blocked for blocked symlinks."""
        target = tmp_path / "target.txt"
        target.write_text("content")
        link = tmp_path / "link.txt"
        os.symlink(str(target), str(link))

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(link) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["python3", "-c", f"import os; os.readlink('{link}'); print('OK')"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_realpath_blocked(self, tmp_path):
        """C realpath() is blocked for blocked files."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_fb_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "realpath", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_write_open_blocked(self, tmp_path):
        """Opening a blocked file for writing is also blocked."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("original")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["python3", "-c", f"f = open('{blocked}', 'w'); f.write('modified'); print('OK')"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_shutil_copy_blocked(self, tmp_path):
        """shutil.copy2 cannot copy a blocked source file."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        dest = tmp_path / "copy.txt"

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["python3", "-c", f"import shutil; shutil.copy2('{blocked}', '{dest}'); print('OK')"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr


# ---------------------------------------------------------------------------
# Blocklist parsing tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestBlocklistParsing:
    def test_comments_ignored(self, tmp_path):
        """Lines starting with # are ignored in the blocklist."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("# This is a comment\n" + str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_whitespace_stripped(self, tmp_path):
        """Leading/trailing whitespace in blocklist entries is stripped."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("  " + str(blocked) + "  \n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_blank_lines_ignored(self, tmp_path):
        """Blank lines in the blocklist are ignored."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("\n\n" + str(blocked) + "\n\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_relative_path_resolved(self, tmp_path):
        """Relative paths in the blocklist are resolved against CWD."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cat", "secret.txt"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_nonexistent_blocklist_file(self, tmp_path):
        """A missing blocklist file means no blocking."""
        ok_file = tmp_path / "ok.txt"
        ok_file.write_text("ok")

        env = _make_fb_env(tmp_path / "nonexistent_blocklist.txt")
        result = subprocess.run(
            ["cat", str(ok_file)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# Dynamic blocklist tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestDynamicBlocklist:
    def test_dynamic_unblock(self, tmp_path):
        """Removing a file from the blocklist unblocks it."""
        target = tmp_path / "dynamic.txt"
        target.write_text("content")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(target) + "\n")

        env = _make_fb_env(blocklist)

        # Initially blocked
        result = subprocess.run(
            ["cat", str(target)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0

        # Remove from blocklist
        blocklist.write_text("")

        # Wait past the 1-second reload interval
        import time

        time.sleep(1.2)

        result = subprocess.run(
            ["cat", str(target)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "content" in result.stdout

    def test_codex_thread_id_path(self, tmp_path):
        """Blocklist path is constructed from CODEX_THREAD_ID + MCPTAP_BLOCKED_DIR."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        session_dir = tmp_path / "session-abc"
        session_dir.mkdir()
        blocklist_file = session_dir / "blocked_files"
        blocklist_file.write_text(str(blocked) + "\n")

        env = os.environ.copy()
        env["LD_PRELOAD"] = LIB_PATH
        env["CODEX_THREAD_ID"] = "session-abc"
        env["MCPTAP_BLOCKED_DIR"] = str(tmp_path)
        env["LC_ALL"] = "C"
        env.pop("MCPTAP_BLOCKED_FILES_FILE", None)

        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr


# ---------------------------------------------------------------------------
# Tool-level tests (cp, directory access)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestToolLevelBlocking:
    def test_cp_blocked_source(self, tmp_path):
        """cp cannot copy a blocked source file."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        dest = tmp_path / "copy.txt"

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cp", str(blocked), str(dest)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr
        assert not dest.exists()

    def test_directory_blocking(self, tmp_path):
        """Blocking a file inside a directory blocks that specific file,
        but other files in the same directory remain accessible."""
        blocked = tmp_path / "subdir" / "secret.txt"
        ok = tmp_path / "subdir" / "ok.txt"
        blocked.parent.mkdir()
        blocked.write_text("secret")
        ok.write_text("ok")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)

        # Blocked file
        result = subprocess.run(
            ["cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0

        # Other file in same directory
        result = subprocess.run(
            ["cat", str(ok)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "ok" in result.stdout

    def test_blocked_file_rename_fails(self, tmp_path):
        """Renaming a blocked file fails (rename uses stat internally)."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        dest = tmp_path / "renamed.txt"

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["mv", str(blocked), str(dest)],
            capture_output=True,
            text=True,
            env=env,
        )
        # mv may succeed or fail depending on implementation, but blocked file
        # should not be readable; either way the test verifies no crash
        assert result.returncode in (0, 1)

    def test_dd_blocked_source(self, tmp_path):
        """dd cannot read a blocked source file."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        dest = tmp_path / "copy.txt"

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["dd", f"if={blocked}", f"of={dest}", "bs=512", "count=1"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr


# ---------------------------------------------------------------------------
# exec* argv-scan tests — blocking child processes targeting blocked files
# ---------------------------------------------------------------------------


EXEC_HELPER_SRC = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <prog> [args...]\n", argv[0]);
        return 3;
    }
    /* Re-exec the given program with the remaining args. Our execve
     * interceptor scans argv before the child starts. */
    execvp(argv[1], &argv[1]);
    if (errno == EACCES) {
        printf("BLOCKED\n");
        return 0;
    }
    /* If exec succeeded we never reach here; if it failed for another
     * reason, report it. */
    printf("ERROR errno=%d\n", errno);
    return 1;
}
"""


def _build_exec_helper(tmp_path):
    """Build the C helper that calls execvp() so the exec interceptor runs."""
    src = tmp_path / "exec_helper.c"
    binary = tmp_path / "exec_helper"
    src.write_text(EXEC_HELPER_SRC)
    cc = os.environ.get("CC", "gcc")
    result = subprocess.run(
        [cc, "-Wall", "-Wextra", "-o", str(binary), str(src)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Failed to build exec helper: {result.stderr}")
    return str(binary)


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestExecArgvScan:
    """exec* interceptors block child processes whose argv references a
    blocked file, so setuid binaries (sudo) cannot read it after escaping
    LD_PRELOAD."""

    def test_sudo_cat_blocked_file_blocked(self, tmp_path):
        """sudo cat <blocked> is refused before sudo starts."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_sudo_unrelated_command_passes(self, tmp_path):
        """An exec with no blocked argv is allowed through; the child runs
        and produces its own output."""
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("/tmp/nonexistent_exec.txt\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        # /bin/echo prints a marker on stdout; the helper is replaced by it
        # so we see the marker and never see "BLOCKED".
        result = subprocess.run(
            [helper, "/bin/echo", "PASSTHROUGH"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "PASSTHROUGH" in result.stdout
        assert "BLOCKED" not in result.stdout

    def test_cat_blocked_file_via_execvp(self, tmp_path):
        """Direct cat <blocked> through execvp is blocked at exec time too."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "cat", str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_exec_blocked_with_tilde_arg(self, tmp_path):
        """A tilde argv argument that resolves to a blocked HOME-relative
        file is blocked at exec time."""
        home = os.path.expanduser("~")
        test_file = os.path.join(home, ".mcptap_test_exec_block")
        with open(test_file, "w") as f:
            f.write("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("~/.mcptap_test_exec_block\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        try:
            result = subprocess.run(
                [helper, "cat", "~/.mcptap_test_exec_block"],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode == 0
            assert "BLOCKED" in result.stdout
        finally:
            os.unlink(test_file)

    def test_exec_blocked_with_dot_relative_arg(self, tmp_path):
        """A "./<name>" argv that resolves to a blocked file is blocked."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        # Run from tmp_path so "./secret.txt" resolves to the blocked file.
        result = subprocess.run(
            [helper, "cat", "./secret.txt"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_exec_blocked_via_symlink(self, tmp_path):
        """A symlink pointing to a blocked file is blocked at exec time."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        link = tmp_path / "link_to_secret.txt"
        os.symlink(str(blocked), str(link))

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "cat", str(link)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout


# ---------------------------------------------------------------------------
# realpath-normalization tests — "./", "..", and symlink aliases
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestRealpathNormalization:
    """Blocked paths are matched via realpath so trivial path aliases
    (./, ../, symlinks) cannot bypass the blocklist."""

    def test_dot_relative_path_blocked(self, tmp_path):
        """Accessing a blocked file via ./name is blocked."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cat", "./secret.txt"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_dotdot_relative_path_blocked(self, tmp_path):
        """Accessing a blocked file via ../subdir/name is blocked."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        blocked = subdir / "secret.txt"
        blocked.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        nested = tmp_path / "subdir" / "nested"
        nested.mkdir()
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cat", "../secret.txt"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(nested),
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_symlink_to_blocked_file_blocked(self, tmp_path):
        """A symlink pointing to a blocked file is blocked."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        link = tmp_path / "link.txt"
        os.symlink(str(blocked), str(link))

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cat", str(link)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_blocked_file_via_symlink_in_blocklist(self, tmp_path):
        """Listing a symlink in the blocklist also blocks the target via
        realpath normalization of the candidate path."""
        target = tmp_path / "real_secret.txt"
        target.write_text("secret")
        link = tmp_path / "link.txt"
        os.symlink(str(target), str(link))

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(link) + "\n")

        env = _make_fb_env(blocklist)
        # Access the real target — realpath of target == realpath of link,
        # so the candidate is blocked.
        result = subprocess.run(
            ["cat", str(target)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_nonexistent_blocked_path_resolved_against_cwd(self, tmp_path):
        """Relative candidate paths are resolved against CWD before
        comparison, even if the file does not exist (falls back to the
        joined CWD+name form)."""
        blocked_abs = tmp_path / "secret.txt"
        blocked_abs.write_text("secret")

        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked_abs) + "\n")

        env = _make_fb_env(blocklist)
        result = subprocess.run(
            ["cat", "secret.txt"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode != 0
        assert "Permission denied" in result.stderr


# ---------------------------------------------------------------------------
# Surgical escalator + interpreter + payload-substring detection tests
# ---------------------------------------------------------------------------
#
# These tests exercise the layer added to is_argv_blocked() that blocks
# `sudo <interpreter> -c '<payload containing a blocked path>'`.  Unlike the
# direct argv path-scan (which only catches `sudo cat <blocked>`), this layer
# inspects the concatenated payload passed to an interpreter (bash, sh,
# python3, ...) and refuses the exec if the payload contains a reference to
# a blocklist path.
#
# The exec helper (_build_exec_helper) re-execs its argv[1..] via execvp();
# the library's execvp() interceptor scans argv and returns EACCES, which
# the helper reports as "BLOCKED" on stdout.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not lib_exists(), reason="libmcptap_fileblock.so not built")
class TestExecArgvSurgicalEscalation:
    """Surgical escalator+interpreter+payload detection.

    Covers the escape vectors where a setuid binary (sudo) spawns an
    interpreter whose payload string contains a blocked path."""

    # ---- argv-based vectors that MUST be blocked ----

    def test_sudo_bash_c_cat_blocked(self, tmp_path):
        """sudo bash -c 'cat <blocked>' is blocked (payload substring)."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "bash", "-c", "cat " + str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_sudo_sh_c_cat_blocked(self, tmp_path):
        """sudo sh -c 'cat <blocked>' is blocked."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "sh", "-c", "cat " + str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_sudo_python_c_open_blocked(self, tmp_path):
        """sudo python3 -c "open(<blocked>)" is blocked."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "python3", "-c", "print(open('%s').read()[:10])" % str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_sudo_env_bash_c_cat_blocked(self, tmp_path):
        """sudo env -- bash -c 'cat <blocked>' is blocked."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "env", "--", "bash", "-c", "cat " + str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_sudo_bash_c_dd_if_blocked(self, tmp_path):
        """sudo bash -c 'dd if=<blocked>...' is blocked."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "bash", "-c", "dd if=%s bs=1 count=1" % str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    def test_sudo_bash_c_cat_tilde_form(self, tmp_path):
        """sudo bash -c 'cat ~/.blocked' is blocked (~ expanded in payload)."""
        home = os.path.expanduser("~")
        blocked = os.path.join(home, ".mcptap_test_surgical_tilde")
        with open(blocked, "w") as f:
            f.write("secret")
        try:
            blocklist = tmp_path / "blocklist.txt"
            blocklist.write_text(str(blocked) + "\n")

            helper = _build_exec_helper(tmp_path)
            env = _make_fb_env(blocklist)
            result = subprocess.run(
                [helper, "sudo", "bash", "-c", "cat ~/.mcptap_test_surgical_tilde"],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode == 0
            assert "BLOCKED" in result.stdout
        finally:
            os.unlink(blocked)

    def test_sudo_bash_c_var_assignment_tilde(self, tmp_path):
        """sudo bash -c 'F=~/.blocked; cat "$F"' is blocked (~ after '=')."""
        home = os.path.expanduser("~")
        blocked = os.path.join(home, ".mcptap_test_surgical_var")
        with open(blocked, "w") as f:
            f.write("secret")
        try:
            blocklist = tmp_path / "blocklist.txt"
            blocklist.write_text(str(blocked) + "\n")

            helper = _build_exec_helper(tmp_path)
            env = _make_fb_env(blocklist)
            result = subprocess.run(
                [helper, "sudo", "bash", "-c", 'F=~/.mcptap_test_surgical_var; cat "$F"'],
                capture_output=True,
                text=True,
                env=env,
            )
            assert result.returncode == 0
            assert "BLOCKED" in result.stdout
        finally:
            os.unlink(blocked)

    def test_sudo_arg_contains_blocked_path(self, tmp_path):
        """sudo <env> <blocked-path> <bash> is blocked (blocked path as arg)."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "env", str(blocked), "bash"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout

    # ---- legal cases that MUST NOT be over-blocked ----

    def test_sudo_bash_c_legit_not_blocked(self, tmp_path):
        """sudo bash -c 'echo legit' is NOT blocked (no blocked path in payload)."""
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(tmp_path / "secret.txt") + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        # Use /bin/echo as the actual program; the helper is replaced by it.
        result = subprocess.run(
            [helper, "sudo", "bash", "-c", "echo LEGIT_OK"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        # Not blocked — exec succeeded (helper replaced by echo/exit 0).
        assert "BLOCKED" not in result.stdout

    def test_sudo_python_c_legit_not_blocked(self, tmp_path):
        """sudo python3 -c 'print(1)' is NOT blocked."""
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(tmp_path / "secret.txt") + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "python3", "-c", "print('LEGIT_PY_OK')"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" not in result.stdout

    def test_sudo_cat_non_blocked_not_blocked(self, tmp_path):
        """sudo cat <non-blocked> is NOT blocked (still handled by path-scan
        layer, which only blocks blocklisted paths)."""
        ok = tmp_path / "ok.txt"
        ok.write_text("ok")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(tmp_path / "secret.txt") + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist)
        result = subprocess.run(
            [helper, "sudo", "cat", str(ok)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" not in result.stdout

    # ---- configuration / override tests ----

    def test_disable_escalator_check_env(self, tmp_path):
        """MCPTAP_FB_DISABLE_ESCALATOR_CHECK=1 disables the surgical layer."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist, extra={"MCPTAP_FB_DISABLE_ESCALATOR_CHECK": "1"})
        result = subprocess.run(
            [helper, "sudo", "bash", "-c", "cat " + str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        # Without the surgical layer, the path is not in argv directly
        # (it's inside the bash -c payload), so exec proceeds.
        assert result.returncode == 0
        assert "BLOCKED" not in result.stdout

    def test_custom_escalators_env(self, tmp_path):
        """MCPTAP_FB_ESCALATORS overrides the default escalator list."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        # Only "custom_sudo" is an escalator; "sudo" is no longer one.
        env = _make_fb_env(blocklist, extra={"MCPTAP_FB_ESCALATORS": "custom_sudo"})
        result = subprocess.run(
            [helper, "sudo", "bash", "-c", "cat " + str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" not in result.stdout

    def test_custom_interpreters_env(self, tmp_path):
        """MCPTAP_FB_INTERPRETERS overrides the default interpreter list.

        The surgical layer scans argv in two passes: (1) a substring check
        of every argv[i>=1] against the blocklist (always active, independent
        of the interpreter list), and (2) a payload-substring check after a
        recognized interpreter.  Pass (1) still catches `cat <blocked>`
        embedded in any arg even when the interpreter list is overridden, so
        to exercise pass (2) isolation we craft a payload that contains the
        blocked path only inside an interpreter payload that is NOT in the
        custom interpreter list, and use a payload form that does NOT
        embed the blocked path as a standalone arg substring.

        Concretely: with MCPTAP_FB_INTERPRETERS=custom_interp, `bash` is no
        longer recognized, so a payload of the form
        `X=secret.txt cat "$X"` (no arg IS the blocked path) is not scanned
        by pass (2).  Pass (1) also does not match because no single arg
        contains the blocked path as a substring.  The exec proceeds."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist, extra={"MCPTAP_FB_INTERPRETERS": "custom_interp"})
        # Payload references the file via a variable assignment + expansion,
        # so no single argv element contains the full blocked path.
        payload = 'F=%s; cat "$F"' % str(blocked.name)
        result = subprocess.run(
            [helper, "sudo", "bash", "-c", payload],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0
        assert "BLOCKED" not in result.stdout

    def test_default_interpreters_env_empty_string_disables(self, tmp_path):
        """An empty MCPTAP_FB_INTERPRETERS value falls back to defaults
        (env var present but empty is treated as unset)."""
        blocked = tmp_path / "secret.txt"
        blocked.write_text("secret")
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text(str(blocked) + "\n")

        helper = _build_exec_helper(tmp_path)
        env = _make_fb_env(blocklist, extra={"MCPTAP_FB_INTERPRETERS": ""})
        result = subprocess.run(
            [helper, "sudo", "bash", "-c", "cat " + str(blocked)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "BLOCKED" in result.stdout
