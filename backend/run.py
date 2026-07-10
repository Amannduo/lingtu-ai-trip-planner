"""Backend launcher."""

import os
import socket
import subprocess
import sys

from app.config import get_settings


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(
                encoding="utf-8",
                errors="backslashreplace",
                line_buffering=True,
                write_through=True
            )
        except Exception:
            continue


def _can_bind(host: str, port: int) -> tuple[bool, str]:
    """Check the port before uvicorn starts so failures are visible and concise."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
        return True, ""
    except OSError as exc:
        return False, str(exc)


def _print_windows_port_owners(port: int) -> None:
    if os.name != "nt":
        return

    command = (
        f"Get-NetTCPConnection -State Listen -LocalPort {port} "
        "-ErrorAction SilentlyContinue | "
        "Select-Object LocalAddress,LocalPort,OwningProcess | Format-Table -AutoSize"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=5
        )
    except Exception as exc:
        print(f"[backend] Could not inspect port owners: {exc}")
        return

    output = (result.stdout or "").strip()
    if output:
        print("[backend] Current listeners on this port:")
        print(output)


def _exit_if_port_busy(host: str, port: int) -> None:
    available, error = _can_bind(host, port)
    if available:
        return

    print(f"[backend] ERROR: cannot bind {host}:{port}")
    print(f"[backend] Reason: {error}")
    _print_windows_port_owners(port)
    print("[backend] Close the old backend terminal/process, then start again.")
    print(
        "[backend] Windows command: "
        f"Get-NetTCPConnection -State Listen -LocalPort {port} | "
        "Select-Object LocalAddress,LocalPort,OwningProcess"
    )
    print("[backend] If you intentionally keep it running, start this backend on another PORT.")
    raise SystemExit(1)


if __name__ == "__main__":
    _configure_stdio()
    settings = get_settings()

    print(f"[backend] interpreter: {sys.executable}")
    print(f"[backend] cwd: {os.getcwd()}")
    print(f"[backend] host: {settings.host}:{settings.port}")
    print(f"[backend] reload: {settings.debug}")

    _exit_if_port_busy(settings.host, settings.port)

    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        if exc.name == "uvicorn":
            print(f"Missing backend dependency 'uvicorn' in interpreter: {sys.executable}")
            print("Use the conda jupyter environment to start the backend.")
            print(r"Example: conda run -n jupyter python backend\run.py")
            raise SystemExit(1)
        raise

    uvicorn.run(
        "app.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )
