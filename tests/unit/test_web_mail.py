"""Invitation and reset emails (SES through the AWS CLI), with the CLI faked."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import datetime

import pytest

from reitstreet.core.config import Settings
from reitstreet.core.errors import ConfigError
from reitstreet.web import mail
from reitstreet.web.admin import Link

LINK = Link("ann@acme.example", "invite", "https://research.test.example/invite/SECRET-TOKEN", datetime(2026, 12, 3, 9))


def _settings(**kw: object) -> Settings:
    s = Settings()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_compose_invite_and_reset() -> None:
    m = mail.compose(LINK)
    assert m.to == "ann@acme.example" and "invited" in m.text and LINK.url in m.text
    assert "2026-12-03 09:00 UTC" in m.text
    r = mail.compose(Link(LINK.email, "password_reset", LINK.url, LINK.expires_at))
    assert r.subject.startswith("Reset")


def test_off_by_default_and_settings_checked() -> None:
    with pytest.raises(ConfigError, match="email is off"):
        mail.send(Settings(), mail.compose(LINK))
    with pytest.raises(ConfigError, match="email_from"):
        mail.send(_settings(web_email_provider="ses"), mail.compose(LINK))
    with pytest.raises(ConfigError, match="email_provider"):
        mail.check_settings(_settings(web_email_provider="smtp"))


def test_ses_send_keeps_link_off_the_command_line() -> None:
    seen: dict[str, object] = {}

    def fake(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        seen["cmd"] = list(cmd)
        path = next(c for c in cmd if c.startswith("file://"))[len("file://") :]
        with open(path, encoding="utf-8") as fh:
            seen["body"] = json.load(fh)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"MessageId": "m-1"}', stderr="")

    s = _settings(web_email_provider="ses", web_email_from="research@test.example", web_email_region="us-east-2")
    assert mail.send(s, mail.compose(LINK), runner=fake) == "m-1"
    cmd = seen["cmd"]
    assert isinstance(cmd, list) and cmd[:3] == ["aws", "sesv2", "send-email"] and "us-east-2" in cmd
    assert not any("SECRET-TOKEN" in c for c in cmd)
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["FromEmailAddress"] == "research@test.example"
    assert body["Destination"] == {"ToAddresses": ["ann@acme.example"]}
    assert "SECRET-TOKEN" in body["Content"]["Simple"]["Body"]["Text"]["Data"]


def test_ses_refusal_is_reported_without_the_body() -> None:
    def fake(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        err = "MessageRejected: Email address is not verified."
        return subprocess.CompletedProcess(cmd, 254, stdout="", stderr=err)

    s = _settings(web_email_provider="ses", web_email_from="research@test.example")
    with pytest.raises(ConfigError, match="not verified") as e:
        mail.send(s, mail.compose(LINK), runner=fake)
    assert "SECRET-TOKEN" not in str(e.value)
