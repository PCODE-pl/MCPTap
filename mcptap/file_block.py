"""File access blocking via LD_PRELOAD integration.

Manages per-session blocklist control files that the LD_PRELOAD library
reads to know which file paths to block.
"""

import os
from typing import List

from mcptap.settings import LOGGER, settings


def blocklist_file_path(session_id: str) -> str:
    """Return the path to the per-session blocklist control file.

    The LD_PRELOAD library reads this file to know which paths to block.
    The path is: <MCP_TAP_PER_SESSION_DIR>/<session_id>/blocked_files
    """
    session_dir = os.path.join(settings.per_session_dir, session_id)
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, "blocked_files")


def write_blocklist(session_id: str, blocked_files: List[str]) -> str:
    """Write the blocked files list to a control file and return its path."""
    path = blocklist_file_path(session_id)
    with open(path, "w") as f:
        for entry in blocked_files:
            f.write(f"{entry}\n")
    LOGGER.info(
        "Blocklist written for session=%s: %d files -> %s",
        session_id,
        len(blocked_files),
        path,
    )
    return path


def clear_blocklist(session_id: str) -> None:
    """Remove the blocklist control file for a session."""
    path = blocklist_file_path(session_id)
    try:
        os.unlink(path)
        LOGGER.info("Blocklist cleared for session=%s", session_id)
    except FileNotFoundError:
        pass
