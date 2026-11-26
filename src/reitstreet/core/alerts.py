"""Alerting. Every alert is written to ops_alert and the log; email (SMTP) and a
webhook (Slack, Teams, or anything that accepts JSON POST) are optional channels.
An alert with the same dedupe_key inside the suppression window is recorded but not
re-sent, so a broken source does not page you every 30 minutes."""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage

import requests

from .config import Settings
from .db import Db

log = logging.getLogger(__name__)
SEVERITIES = ("info", "warn", "error", "critical")


class Alerter:
    def __init__(self, db: Db, settings: Settings, suppress_hours: float = 6.0):
        self.db = db
        self.s = settings
        self.suppress_hours = suppress_hours
        self.sent: list[dict[str, str]] = []

    def raise_alert(
        self, severity: str, source: str, title: str, body: str = "", dedupe_key: str | None = None
    ) -> bool:
        """Record and deliver. Returns True if delivered (not suppressed)."""
        assert severity in SEVERITIES
        key = (dedupe_key or f"{source}:{title}")[:191]
        recent = self.db.scalar(
            """SELECT COUNT(*) FROM ops_alert WHERE dedupe_key=:k AND suppressed=0
               AND created_at > CURRENT_TIMESTAMP(6) - INTERVAL :h HOUR""",
            {"k": key, "h": self.suppress_hours},
        )
        suppressed = bool(recent)
        channels, errors = ["log"], []
        logger = log.error if severity in ("error", "critical") else log.warning
        logger("ALERT %s", title, extra={"severity": severity, "source": source, "suppressed": suppressed})
        if not suppressed and SEVERITIES.index(severity) >= SEVERITIES.index(self.s.alerts_min_severity):
            if self.s.alerts_smtp_host and self.s.alerts_to:
                try:
                    self._email(severity, title, body)
                    channels.append("email")
                except Exception as e:
                    errors.append(f"email: {e}")
            if self.s.alerts_webhook_url:
                try:
                    self._webhook(severity, source, title, body)
                    channels.append("webhook")
                except Exception as e:
                    errors.append(f"webhook: {e}")
        self.db.execute(
            """INSERT INTO ops_alert (severity, source, title, body, dedupe_key, channels, suppressed, delivery_error)
               VALUES (:sev, :src, :t, :b, :k, :ch, :sup, :err)""",
            {
                "sev": severity,
                "src": source[:64],
                "t": title[:255],
                "b": body[:60000] or None,
                "k": key,
                "ch": ",".join(channels),
                "sup": int(suppressed),
                "err": "\n".join(errors) or None,
            },
        )
        self.db.commit()
        if not suppressed:
            self.sent.append({"severity": severity, "title": title})
        return not suppressed

    def _email(self, severity: str, title: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"[reitstreet {severity.upper()}] {title}"
        msg["From"] = self.s.alerts_from or self.s.alerts_smtp_user
        msg["To"] = self.s.alerts_to
        msg.set_content(body or title)
        with smtplib.SMTP(self.s.alerts_smtp_host, self.s.alerts_smtp_port, timeout=30) as smtp:
            smtp.starttls()
            if self.s.alerts_smtp_user:
                smtp.login(self.s.alerts_smtp_user, self.s.alerts_smtp_password)
            smtp.send_message(msg)

    def _webhook(self, severity: str, source: str, title: str, body: str) -> None:
        payload = {
            "text": f"[reitstreet {severity.upper()}] {title}\n{body[:3000]}",
            "severity": severity,
            "source": source,
            "title": title,
        }
        r = requests.post(
            self.s.alerts_webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        r.raise_for_status()
