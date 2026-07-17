"""Privilege-dropping entrypoint used only by untrusted Modal Sandboxes."""

from __future__ import annotations

import ctypes
import os
import sys

SANDBOX_UID = 65532
SANDBOX_GID = 65532
PR_SET_NO_NEW_PRIVS = 38


def _drop_privileges() -> None:
    """Prevent privilege regain, remove supplementary groups, and become an unprivileged user."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))
    os.setgroups([])
    os.setgid(SANDBOX_GID)
    os.setuid(SANDBOX_UID)
    os.umask(0o077)


def main() -> None:
    """Execute the requested component only after establishing the restricted identity."""
    if len(sys.argv) < 2:
        raise SystemExit("sandbox command is required")
    _drop_privileges()
    os.chdir("/tmp")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
