"""macOS launchd helpers for x-bookmarks watch mode."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from bookmark_paths import resolve_base_dir

DEFAULT_LABEL = "com.yogevkr.x-bookmarks.watch"
DEFAULT_EXPORT_LABEL = "com.yogevkr.x-bookmarks.export"
DEFAULT_STALE_CHECK_LABEL = "com.yogevkr.x-bookmarks.stale-check"


def _ensure_macos() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("launchd commands are only available on macOS")


def _launch_agent_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def launch_agent_path(*, label: str = DEFAULT_LABEL) -> Path:
    return _launch_agent_dir() / f"{label}.plist"


def _entrypoint_args() -> list[str]:
    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.is_absolute() and argv0.name == "x-bookmarks" and argv0.exists():
        return [str(argv0)]
    if argv0.name == "x-bookmarks":
        resolved = shutil.which("x-bookmarks")
        if resolved:
            return [resolved]
    resolved = shutil.which("x-bookmarks")
    if resolved:
        return [resolved]
    return [sys.executable, str((Path(__file__).resolve().parent / "main.py"))]


def build_launch_agent_plist(
    *,
    label: str = DEFAULT_LABEL,
    interval: float = 5.0,
    base_dir: Path | None = None,
    quiet: bool = True,
) -> dict:
    _ensure_macos()
    runtime_base = (base_dir or resolve_base_dir()).expanduser().resolve()
    data_dir = runtime_base / ".x-bookmarks"
    data_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = data_dir / "watch.stdout.log"
    stderr_path = data_dir / "watch.stderr.log"
    args = [*_entrypoint_args(), "watch", "--interval", f"{interval:g}"]
    if quiet:
        args.append("--quiet")

    return {
        "Label": label,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "WorkingDirectory": str(runtime_base),
        "EnvironmentVariables": {
            "X_BOOKMARKS_HOME": str(runtime_base),
        },
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "ProcessType": "Background",
    }


def build_export_launch_agent_plist(
    *,
    label: str = DEFAULT_EXPORT_LABEL,
    interval: int = 24 * 60 * 60,
    base_dir: Path | None = None,
    user_data_dir: Path | None = None,
    debug_port: int = 9223,
    timeout: int = 900,
    quiet: bool = True,
) -> dict:
    _ensure_macos()
    runtime_base = (base_dir or resolve_base_dir()).expanduser().resolve()
    data_dir = runtime_base / ".x-bookmarks"
    data_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = data_dir / "export.stdout.log"
    stderr_path = data_dir / "export.stderr.log"
    args = [
        *_entrypoint_args(),
        "export-x",
        "--sync",
        "--no-extract",
        "--debug-port",
        str(debug_port),
        "--timeout",
        str(timeout),
    ]
    if user_data_dir:
        args.extend(["--user-data-dir", str(user_data_dir.expanduser())])
    if quiet:
        args.append("--quiet")

    return {
        "Label": label,
        "ProgramArguments": args,
        "RunAtLoad": False,
        "StartInterval": interval,
        "ThrottleInterval": 300,
        "WorkingDirectory": str(runtime_base),
        "EnvironmentVariables": {
            "X_BOOKMARKS_HOME": str(runtime_base),
        },
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "ProcessType": "Background",
    }


def build_stale_check_launch_agent_plist(
    *,
    label: str = DEFAULT_STALE_CHECK_LABEL,
    interval: int = 6 * 60 * 60,
    base_dir: Path | None = None,
    max_age_hours: float = 36.0,
    alert_every_hours: float = 24.0,
    quiet: bool = True,
) -> dict:
    _ensure_macos()
    runtime_base = (base_dir or resolve_base_dir()).expanduser().resolve()
    data_dir = runtime_base / ".x-bookmarks"
    data_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = data_dir / "stale-check.stdout.log"
    stderr_path = data_dir / "stale-check.stderr.log"
    args = [
        *_entrypoint_args(),
        "stale-check",
        "--max-age-hours",
        f"{max_age_hours:g}",
        "--alert-every-hours",
        f"{alert_every_hours:g}",
        "--notify",
    ]
    if quiet:
        args.append("--quiet")

    return {
        "Label": label,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "StartInterval": interval,
        "ThrottleInterval": 300,
        "WorkingDirectory": str(runtime_base),
        "EnvironmentVariables": {
            "X_BOOKMARKS_HOME": str(runtime_base),
        },
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "ProcessType": "Background",
    }


def write_launch_agent(
    *,
    label: str = DEFAULT_LABEL,
    interval: float = 5.0,
    base_dir: Path | None = None,
    quiet: bool = True,
) -> Path:
    plist_path = launch_agent_path(label=label)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_launch_agent_plist(label=label, interval=interval, base_dir=base_dir, quiet=quiet)
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    return plist_path


def write_export_launch_agent(
    *,
    label: str = DEFAULT_EXPORT_LABEL,
    interval: int = 24 * 60 * 60,
    base_dir: Path | None = None,
    user_data_dir: Path | None = None,
    debug_port: int = 9223,
    timeout: int = 900,
    quiet: bool = True,
) -> Path:
    plist_path = launch_agent_path(label=label)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_export_launch_agent_plist(
        label=label,
        interval=interval,
        base_dir=base_dir,
        user_data_dir=user_data_dir,
        debug_port=debug_port,
        timeout=timeout,
        quiet=quiet,
    )
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    return plist_path


def write_stale_check_launch_agent(
    *,
    label: str = DEFAULT_STALE_CHECK_LABEL,
    interval: int = 6 * 60 * 60,
    base_dir: Path | None = None,
    max_age_hours: float = 36.0,
    alert_every_hours: float = 24.0,
    quiet: bool = True,
) -> Path:
    plist_path = launch_agent_path(label=label)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_stale_check_launch_agent_plist(
        label=label,
        interval=interval,
        base_dir=base_dir,
        max_age_hours=max_age_hours,
        alert_every_hours=alert_every_hours,
        quiet=quiet,
    )
    with plist_path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    return plist_path


def _launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def install_launch_agent(
    *,
    label: str = DEFAULT_LABEL,
    interval: float = 5.0,
    base_dir: Path | None = None,
    quiet: bool = True,
) -> dict:
    _ensure_macos()
    plist_path = write_launch_agent(label=label, interval=interval, base_dir=base_dir, quiet=quiet)
    subprocess.run(["launchctl", "bootout", _launchctl_domain(), str(plist_path)], capture_output=True, text=True)
    subprocess.run(["launchctl", "bootstrap", _launchctl_domain(), str(plist_path)], check=True, capture_output=True, text=True)
    subprocess.run(["launchctl", "kickstart", "-k", f"{_launchctl_domain()}/{label}"], capture_output=True, text=True)
    return launch_agent_status(label=label)


def install_export_launch_agent(
    *,
    label: str = DEFAULT_EXPORT_LABEL,
    interval: int = 24 * 60 * 60,
    base_dir: Path | None = None,
    user_data_dir: Path | None = None,
    debug_port: int = 9223,
    timeout: int = 900,
    quiet: bool = True,
) -> dict:
    _ensure_macos()
    plist_path = write_export_launch_agent(
        label=label,
        interval=interval,
        base_dir=base_dir,
        user_data_dir=user_data_dir,
        debug_port=debug_port,
        timeout=timeout,
        quiet=quiet,
    )
    subprocess.run(["launchctl", "bootout", _launchctl_domain(), str(plist_path)], capture_output=True, text=True)
    subprocess.run(["launchctl", "bootstrap", _launchctl_domain(), str(plist_path)], check=True, capture_output=True, text=True)
    return launch_agent_status(label=label)


def install_stale_check_launch_agent(
    *,
    label: str = DEFAULT_STALE_CHECK_LABEL,
    interval: int = 6 * 60 * 60,
    base_dir: Path | None = None,
    max_age_hours: float = 36.0,
    alert_every_hours: float = 24.0,
    quiet: bool = True,
) -> dict:
    _ensure_macos()
    plist_path = write_stale_check_launch_agent(
        label=label,
        interval=interval,
        base_dir=base_dir,
        max_age_hours=max_age_hours,
        alert_every_hours=alert_every_hours,
        quiet=quiet,
    )
    subprocess.run(["launchctl", "bootout", _launchctl_domain(), str(plist_path)], capture_output=True, text=True)
    subprocess.run(["launchctl", "bootstrap", _launchctl_domain(), str(plist_path)], check=True, capture_output=True, text=True)
    return launch_agent_status(label=label)


def uninstall_launch_agent(*, label: str = DEFAULT_LABEL) -> dict:
    _ensure_macos()
    plist_path = launch_agent_path(label=label)
    subprocess.run(["launchctl", "bootout", _launchctl_domain(), str(plist_path)], capture_output=True, text=True)
    if plist_path.exists():
        plist_path.unlink()
    return {
        "label": label,
        "plist_path": str(plist_path),
        "installed": False,
        "loaded": False,
    }


def _parse_launchctl_output(output: str) -> dict:
    parsed: dict[str, object] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("pid = "):
            try:
                parsed["pid"] = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("state = "):
            parsed["state"] = line.split("=", 1)[1].strip()
        elif line.startswith("last exit code = "):
            try:
                parsed["last_exit_code"] = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    return parsed


def launch_agent_status(*, label: str = DEFAULT_LABEL) -> dict:
    _ensure_macos()
    plist_path = launch_agent_path(label=label)
    status = {
        "label": label,
        "plist_path": str(plist_path),
        "installed": plist_path.exists(),
        "loaded": False,
        "pid": None,
        "state": None,
        "last_exit_code": None,
    }
    result = subprocess.run(
        ["launchctl", "print", f"{_launchctl_domain()}/{label}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return status
    status["loaded"] = True
    status.update(_parse_launchctl_output(result.stdout))
    return status
