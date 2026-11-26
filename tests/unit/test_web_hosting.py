"""Checks on the website hosting templates (deploy/aws/web, ADR 0013).

They are CloudFormation and are not deployed by CI; these tests catch the mistakes that
would only show up in his AWS account: a broken setup script, a secret in user data, a
second place that names the domain.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "deploy" / "aws" / "web"
TEMPLATES = ["web-fargate.yaml", "web-ec2.yaml"]
TEST_DOMAIN = "research.jwalteranalytics.com"


class _Loader(yaml.SafeLoader):
    """Reads CloudFormation short-form tags (!Ref, !Sub, ...) as plain {"Fn": value}."""


def _tag(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)  # type: ignore[arg-type]
    return {suffix: value}


_Loader.add_multi_constructor("!", _tag)


def load(name: str) -> dict[str, Any]:
    with (WEB / name).open(encoding="utf-8") as f:
        return yaml.load(f, Loader=_Loader)  # noqa: S506  custom SafeLoader subclass


@pytest.mark.parametrize("name", TEMPLATES)
def test_domain_is_one_parameter(name: str) -> None:
    t = load(name)
    assert t["Parameters"]["DomainName"]["Default"] == TEST_DOMAIN
    text = (WEB / name).read_text(encoding="utf-8")
    assert text.count(TEST_DOMAIN) == 1, "the domain appears only as the parameter default"


@pytest.mark.parametrize("name", TEMPLATES)
def test_starts_safe(name: str) -> None:
    p = load(name)["Parameters"]
    assert p["EmailProvider"]["Default"] == "none"
    assert p["SampleData"]["Default"] == "1"
    assert p["EnvName"]["Default"] == "staging"
    assert p["ImageTag"]["Default"] == "none"


@pytest.mark.parametrize("name", TEMPLATES)
def test_database_reached_only_from_the_site(name: str) -> None:
    r = load(name)["Resources"]["DbIngressFromSite"]["Properties"]
    assert r["FromPort"] == r["ToPort"] == 3306
    assert "SourceSecurityGroupId" in r and "CidrIp" not in r


def test_fargate_secrets_come_from_secrets_manager() -> None:
    c = load("web-fargate.yaml")["Resources"]["TaskDefinition"]["Properties"]["ContainerDefinitions"][0]
    secret_names = {s["Name"] for s in c["Secrets"]}
    env_names = {e["Name"] for e in c["Environment"] if isinstance(e, dict) and "Name" in e}
    assert {"RS_DB_PASSWORD", "RS_DB_USER", "RS_DB_HOST"} <= secret_names
    assert not secret_names & env_names
    assert load("web-fargate.yaml")["Resources"]["Service"]["Properties"]["DesiredCount"] == {"Ref": "DesiredCount"}


def _user_data() -> str:
    ud = load("web-ec2.yaml")["Resources"]["Instance"]["Properties"]["UserData"]["Fn::Base64"]["Sub"]
    # what CloudFormation would render: ${Name} and ${X.Y} replaced, ${!x} becomes ${x}
    rendered = re.sub(r"\$\{(?!!)[A-Za-z0-9:.]+\}", "VALUE", ud)
    return rendered.replace("${!", "${")


def test_ec2_user_data_holds_no_secret_and_parses() -> None:
    ud = _user_data()
    assert "RS_DB_PASSWORD=" in ud and "\\(.password)" in ud, "password is read at start, not written in"
    assert "umask 077" in ud
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    subprocess.run([bash, "-n"], input=ud, text=True, check=True)  # noqa: S603  fixed argv
