"""Email for the client website's invitation and reset links (ADR 0013).

Off by default (`[web] email_provider = none`): staff copy the printed link to the person.
With `ses`, a message is sent with Amazon SES v2 through the AWS CLI, using whatever role
the machine has (the self-hosted runner's instance role). No keys are stored or read here.

The link is a credential. It goes into the message body and nowhere else: not into logs,
command output or exceptions.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from ..core.config import Settings
from ..core.errors import ConfigError
from .admin import EMAIL_RE, Link

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
PROVIDERS = ("none", "ses")


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=60, check=False)  # noqa: S603  fixed argv, no shell


@dataclass
class Message:
    to: str
    subject: str
    text: str


def compose(link: Link, site_name: str = "REIT Street") -> Message:
    """Plain-text invitation or reset message. Wording is PLACEHOLDER copy (docs/web-copy-review.md)."""
    expires = _fmt(link.expires_at)
    if link.purpose == "invite":
        subject = f"Your {site_name} account"
        lead = f"You have been invited to {site_name}. Open this link to choose a password and sign in:"
    else:
        subject = f"Reset your {site_name} password"
        lead = "Open this link to choose a new password:"
    text = (
        f"{lead}\n\n{link.url}\n\n"
        f"The link works once and expires {expires} UTC.\n"
        "If you did not expect this email, you can ignore it.\n"
    )
    return Message(link.email, subject, text)


def _fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M")


def check_settings(s: Settings) -> None:
    if s.web_email_provider not in PROVIDERS:
        raise ConfigError(f"[web] email_provider must be one of {', '.join(PROVIDERS)}")
    if s.web_email_provider == "ses" and not EMAIL_RE.match(s.web_email_from or ""):
        raise ConfigError("[web] email_from must be an address on a domain verified in SES")


def send(s: Settings, msg: Message, *, runner: Runner = _run) -> str:
    """Sends one message; returns the SES message id. Raises ConfigError when email is off."""
    check_settings(s)
    if s.web_email_provider == "none":
        raise ConfigError("email is off ([web] email_provider = none): send the printed link yourself")
    if not EMAIL_RE.match(msg.to):
        raise ConfigError("not an email address")
    body = {
        "FromEmailAddress": s.web_email_from,
        "Destination": {"ToAddresses": [msg.to]},
        "Content": {
            "Simple": {
                "Subject": {"Data": msg.subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": msg.text, "Charset": "UTF-8"}},
            }
        },
    }
    # the body holds the link: pass it in a private temporary file, never on the command line
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "message.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        cmd = ["aws", "sesv2", "send-email", "--region", s.web_email_region, "--output", "json"]
        cmd += ["--cli-input-json", f"file://{path}"]
        try:
            r = runner(cmd)
        except FileNotFoundError as e:
            raise ConfigError("the AWS CLI is not installed on this machine") from e
    if r.returncode != 0:
        # SES errors name the problem (unverified sender, sandbox recipient); they never echo the body
        raise ConfigError(f"SES refused the message: {(r.stderr or '').strip()[:300]}")
    try:
        return str(json.loads(r.stdout or "{}").get("MessageId", ""))
    except json.JSONDecodeError:
        return ""
