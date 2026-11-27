"""One-time database bootstrap on Amazon Aurora MySQL (run by the ops-bootstrap-db workflow).

Reads the cluster's master credentials from AWS Secrets Manager, then:
  1. checks the server is MySQL 8 compatible (Aurora MySQL 3.x),
  2. creates the `reitstreet` warehouse schema and the `reitstreet_app` web schema (ADR 0013),
  3. creates four users that can only touch those schemas, each with a random password
     that goes straight into Secrets Manager (reitstreet/db/<role>) and is never printed:
       rs_migrator  schema changes (Alembic) on both schemas
       rs_loader    data loads (warehouse), and the `reitstreet web` admin commands (web schema)
       rs_reader    warehouse read only, for analysts; no access to the web schema
       rs_web       the client website: reads the warehouse; reads and writes the web schema,
                    but cannot delete (download and audit logs are append-only)
All users must connect over TLS. Safe to re-run: Secrets Manager is the source of truth,
so an existing secret's password is re-applied, never replaced.

Needs: AWS CLI with credentials (the runner's instance role), PyMySQL.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile

import pymysql

LOADER = ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE TEMPORARY TABLES", "LOCK TABLES", "EXECUTE"]

# role -> [(privileges, schema key)]; schema keys are "wh" (warehouse) and "app" (web)
ROLES: dict[str, list[tuple[list[str], str]]] = {
    "migrator": [(["ALL PRIVILEGES"], "wh"), (["ALL PRIVILEGES"], "app")],
    "loader": [(LOADER, "wh"), (["SELECT", "INSERT", "UPDATE"], "app")],
    "reader": [(["SELECT", "SHOW VIEW"], "wh")],
    "web": [(["SELECT", "SHOW VIEW"], "wh"), (["SELECT", "INSERT", "UPDATE"], "app")],
}


def grant_statements(role: str, schemas: dict[str, str]) -> list[str]:
    """GRANT statements for one role. Schema names come from our own arguments, never user input."""
    user = f"rs_{role}"
    return [f"GRANT {', '.join(privs)} ON `{schemas[key]}`.* TO '{user}'@'%'" for privs, key in ROLES[role]]


def user_statements(user: str) -> list[str]:
    """CREATE USER and ALTER USER for one login, each with one %s for the password.

    These run with a parameter, so PyMySQL %-formats them: the host wildcard must be written
    '%%' to reach the server as '%'. GRANT statements run without parameters and keep '%'.
    """
    return [
        f"CREATE USER IF NOT EXISTS '{user}'@'%%' IDENTIFIED BY %s REQUIRE SSL",
        f"ALTER USER '{user}'@'%%' IDENTIFIED BY %s REQUIRE SSL",
    ]


def aws(region: str, *args: str) -> str:
    cmd = ["aws", "--region", region, "--output", "json", *args]
    res = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603  (fixed binary, no shell)
    if res.returncode != 0:
        raise RuntimeError(f"aws {args[0]} {args[1]} failed: {res.stderr.strip()}")
    return res.stdout


def get_secret(region: str, secret_id: str) -> dict[str, str] | None:
    try:
        out = aws(region, "secretsmanager", "get-secret-value", "--secret-id", secret_id)
    except RuntimeError as e:
        if "ResourceNotFoundException" in str(e):
            return None
        raise
    value: dict[str, str] = json.loads(json.loads(out)["SecretString"])
    return value


def create_secret(region: str, name: str, value: dict[str, object]) -> None:
    # value goes through a private temp file, not the command line, so it never shows in `ps`
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "secret.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(value, fh)
        aws(
            region,
            "secretsmanager",
            "create-secret",
            "--name",
            name,
            "--description",
            "REIT Street database login (created by deploy/aws/bootstrap_db.py)",
            "--secret-string",
            f"file://{path}",
            "--tags",
            "Key=app,Value=reitstreet",
        )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--region", required=True)
    p.add_argument("--master-secret-id", required=True, help="ARN or name of the cluster's master user secret")
    p.add_argument("--host", required=True, help="cluster writer endpoint")
    p.add_argument("--port", type=int, default=3306)
    p.add_argument("--schema", default="reitstreet")
    p.add_argument("--app-schema", default="reitstreet_app")
    p.add_argument("--ca", default="/etc/reitstreet/rds-global-bundle.pem")
    a = p.parse_args()

    master = get_secret(a.region, a.master_secret_id)
    if not master or not master.get("username") or not master.get("password"):
        print("master secret not found or has no username/password", file=sys.stderr)
        return 2

    conn = pymysql.connect(
        host=a.host,
        port=a.port,
        user=master["username"],
        password=master["password"],
        ssl={"ca": a.ca, "check_hostname": True},
        connect_timeout=15,
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION()")
        version = str(cur.fetchone()[0])
        try:
            cur.execute("SELECT AURORA_VERSION()")
            aurora = str(cur.fetchone()[0])
        except pymysql.MySQLError:
            aurora = "n/a"
        print(f"server MySQL {version}, Aurora {aurora}")
        if not version.startswith("8."):
            print("MySQL 8 compatible engine required (Aurora MySQL 3.x). Upgrade the cluster first.", file=sys.stderr)
            return 2

        schemas = {"wh": a.schema, "app": a.app_schema}
        for schema in schemas.values():
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
        for role in ROLES:
            user = f"rs_{role}"
            name = f"reitstreet/db/{role}"
            existing = get_secret(a.region, name)
            if existing:
                password = existing["password"]
                state = "secret existed, password re-applied"
            else:
                password = secrets.token_urlsafe(32)
                create_secret(
                    a.region,
                    name,
                    {
                        "engine": "mysql",
                        "username": user,
                        "password": password,
                        "host": a.host,
                        "port": a.port,
                        "dbname": a.app_schema if role == "web" else a.schema,
                    },
                )
                state = "secret created"
            for stmt in user_statements(user):
                cur.execute(stmt, (password,))
            for stmt in grant_statements(role, schemas):
                cur.execute(stmt)
            granted = "; ".join(f"{', '.join(pv)} on {schemas[k]}" for pv, k in ROLES[role])
            print(f"{user:<12} {granted}: {state} ({name})")
            check = pymysql.connect(
                host=a.host,
                port=a.port,
                user=user,
                password=password,
                database=a.schema,
                ssl={"ca": a.ca, "check_hostname": True},
                connect_timeout=15,
            )
            with check.cursor() as c2:
                c2.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                row = c2.fetchone()
                cipher = row[1] if row else ""
            check.close()
            if not cipher:
                print(f"{user} connected without TLS", file=sys.stderr)
                return 1
            print(f"{user:<12} login checked over TLS ({cipher})")
    conn.close()
    print("bootstrap done. Remove the temporary bootstrap policy from the runner role now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
