"""本地人工处理提示事件。"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATTENTION_TITLES = {
    "CAPTCHA_REQUIRED": "需要人工验证码",
    "RATE_LIMIT": "采集被限流保护",
    "MANUAL_ACTION_REQUIRED": "需要人工处理",
    "AUTOMATION_FAILED": "自动采集需要处理",
}


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def _send_macos_notification(title: str, body: str) -> bool:
    script = f'display notification "{_escape_applescript(body)}" with title "{_escape_applescript(title)}"'
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _send_windows_notification(title: str, body: str) -> bool:
    """使用 Windows 自带 PowerShell/WinForms 气泡通知。"""

    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Warning
$notify.BalloonTipTitle = {json.dumps(title, ensure_ascii=False)}
$notify.BalloonTipText = {json.dumps(body, ensure_ascii=False)}
$notify.Visible = $true
$notify.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$notify.Dispose()
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        return False
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
            check=False,
            capture_output=True,
            timeout=12,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _send_linux_notification(title: str, body: str) -> bool:
    notify_send = shutil.which("notify-send")
    if not notify_send:
        return False
    try:
        result = subprocess.run(
            [notify_send, title, body],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def send_desktop_notification(*, kind: str, message: str, source: str) -> bool:
    """调用当前操作系统的原生通知；不可用时安全返回 False。"""

    title = ATTENTION_TITLES.get(kind, "需要人工处理")
    body = f"{source.strip()}：{message.strip()}".strip("：")
    if sys.platform == "darwin":
        return _send_macos_notification(title, body)
    if os.name == "nt":
        return _send_windows_notification(title, body)
    if sys.platform.startswith("linux"):
        return _send_linux_notification(title, body)
    return False


def write_attention(
    path: str | Path,
    *,
    kind: str,
    message: str,
    source: str,
    url: str,
    notify: bool = True,
) -> Path:
    """写入一个待人工处理事件，不包含凭据或页面正文。"""

    attention_path = Path(path)
    attention_path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_attention(attention_path)
    if existing and all(
        existing.get(key) == value
        for key, value in {
            "kind": kind,
            "message": message.strip(),
            "source": source.strip(),
            "url": url.strip(),
        }.items()
    ):
        return attention_path
    payload = {
        "status": "OPEN",
        "kind": kind,
        "message": message.strip(),
        "source": source.strip(),
        "url": url.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    attention_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if notify:
        send_desktop_notification(kind=kind, message=payload["message"], source=payload["source"])
    return attention_path


def read_attention(path: str | Path) -> dict[str, Any] | None:
    """读取仍处于 OPEN 状态的提示事件。"""

    attention_path = Path(path)
    try:
        payload = json.loads(attention_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "OPEN":
        return None
    return payload


def clear_attention(path: str | Path) -> None:
    """将提示标记为已处理；保留文件便于审计。"""

    attention_path = Path(path)
    current = read_attention(attention_path)
    if current is None:
        return
    current["status"] = "CLEARED"
    current["cleared_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    attention_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
