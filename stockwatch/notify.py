"""Notification channels. Credentials come from environment variables (see .env.example)."""
from __future__ import annotations

import logging
import os
import platform
import shutil
import smtplib
import subprocess
from email.message import EmailMessage

import requests

log = logging.getLogger(__name__)


class NotifierError(RuntimeError):
    pass


def console(title: str, body: str) -> None:
    print(f"\n*** {title} ***\n{body}\n", flush=True)


def desktop(title: str, body: str) -> None:
    system = platform.system()
    if system == "Darwin":
        script = f"display notification {_osa(body)} with title {_osa(title)}"
        subprocess.run(["osascript", "-e", script], check=True)
    elif system == "Linux" and shutil.which("notify-send"):
        subprocess.run(["notify-send", "-u", "critical", title, body], check=True)
    elif system == "Windows":
        # Balloon tip via built-in .NET; no extra install needed.
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Warning;$n.Visible=$true;"
            f"$n.ShowBalloonTip(10000,{_ps(title)},{_ps(body)},'Warning');"
            "Start-Sleep -Seconds 10;$n.Dispose()"
        )
        subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
    else:
        raise NotifierError(f"Desktop notifications not supported on {system}")


def _osa(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ps(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def ntfy(title: str, body: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        raise NotifierError("NTFY_TOPIC is not set")
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    resp = requests.post(
        f"{server}/{topic}",
        data=body.encode("utf-8"),
        headers={"Title": title.encode("utf-8"), "Priority": "high",
                 "Tags": "chart_with_downwards_trend"},
        timeout=15,
    )
    resp.raise_for_status()


def email(title: str, body: str) -> None:
    need = ["SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "ALERT_EMAIL_TO"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise NotifierError(f"Email not configured, missing: {', '.join(missing)}")
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content(body)
    port = int(os.environ.get("SMTP_PORT", "587"))
    if port == 465:
        server = smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, timeout=30)
    else:
        server = smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=30)
        server.starttls()
    with server:
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        server.send_message(msg)


CHANNELS = {"console": console, "desktop": desktop, "ntfy": ntfy, "email": email}


def send(channels: list[str], title: str, body: str) -> dict[str, str | None]:
    """Send on every channel. One failing channel never blocks the others.
    Returns {channel: error message or None}."""
    results: dict[str, str | None] = {}
    for name in channels:
        try:
            CHANNELS[name](title, body)
            results[name] = None
        except Exception as e:  # noqa: BLE001 - report and carry on
            log.warning("Notifier %s failed: %s", name, e)
            results[name] = str(e)
    return results
