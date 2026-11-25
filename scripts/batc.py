"""Minimal MTS Exolve BATC CRM API client for the caller project.

Reads ``MTC_API_KEY`` and ``MTC_ATC_URL`` from the project ``.env``; never
prints the API key or the generated SIP password.

Usage:
    python scripts/batc.py info
    python scripts/batc.py create-agent
    python scripts/batc.py agent
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

AGENT_NAME = "AI Sales Agent"
AGENT_POSITION = "AI Sales Agent"
PASSWORD_ALPHABET = string.ascii_letters + string.digits


def _sip_login(login: str) -> str:
    """Return the SIP/API login without the @login.mtt.ru suffix."""
    return login.split("@", 1)[0].strip()


def _client() -> httpx.Client:
    cfg = dotenv_values(ENV_PATH)
    base = (cfg.get("MTC_ATC_URL") or os.getenv("MTC_ATC_URL") or "").strip()
    key = (cfg.get("MTC_API_KEY") or os.getenv("MTC_API_KEY") or "").strip()
    if not base or not key:
        raise SystemExit("MTC_ATC_URL and MTC_API_KEY must be set in .env")
    return httpx.Client(
        base_url=base.rstrip("/"),
        headers={"X-API-KEY": key},
        timeout=20.0,
    )


def _request(client: httpx.Client, method: str, path: str, **kwargs) -> object:
    response = client.request(method, path, **kwargs)
    if response.status_code >= 400:
        print(f"HTTP {response.status_code}: {response.text}", file=sys.stderr)
        raise SystemExit(1)
    if not response.content:
        return None
    return response.json()


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def cmd_info(args: argparse.Namespace) -> None:
    with _client() as client:
        users = _request(client, "GET", "/users")
        numbers = _request(client, "GET", "/telnums")
        domain = _request(client, "GET", "/domain")

    print("users:")
    for user in users.get("items", []):
        forwarding = user.get("calls_forwarding") or {}
        print(
            f"  - {user.get('login')}  ext={user.get('ext')}  "
            f"role={user.get('role')}  sip_forwarding={forwarding.get('sip')}"
        )

    print("numbers:")
    for number in numbers.get("items", []):
        print(
            f"  - {number.get('telnum')}  type={number.get('type')}  "
            f"user={number.get('user')}  disabled={number.get('disabled')}"
        )

    print("domain:")
    _print_json(domain)


def cmd_create_agent(args: argparse.Namespace) -> None:
    cfg = dotenv_values(ENV_PATH)
    existing = (cfg.get("BATC_AGENT_LOGIN") or "").strip()
    if existing and not args.force:
        print(
            f"BATC_AGENT_LOGIN is already set ({existing}); "
            "use --force to create another employee",
            file=sys.stderr,
        )
        raise SystemExit(2)

    password = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(32))
    payload = {
        "name": AGENT_NAME,
        "position": AGENT_POSITION,
        "password": password,
        "role": "user",
        "calls_forwarding": {"sip": True, "mobile": False},
        "timeout_forwarding": {"timeout": 30, "type": "hangup"},
        "schedule": {"type": "aroundTheClock"},
    }

    with _client() as client:
        created = _request(client, "POST", "/users", json=payload)

    login = str(created.get("login") or "").strip()
    if not login:
        print("BATC returned no login for the new employee", file=sys.stderr)
        _print_json(created)
        raise SystemExit(1)

    set_key(ENV_PATH, "BATC_AGENT_LOGIN", login)
    set_key(ENV_PATH, "BATC_AGENT_PASSWORD", password)
    set_key(ENV_PATH, "BATC_AGENT_EXT", str(created.get("ext") or ""))
    set_key(ENV_PATH, "BATC_AGENT_NAME", str(created.get("name") or AGENT_NAME))

    print(f"created employee: login={login} ext={created.get('ext')!r}")
    print("credentials written to .env as BATC_AGENT_LOGIN / BATC_AGENT_PASSWORD")


def cmd_agent(args: argparse.Namespace) -> None:
    cfg = dotenv_values(ENV_PATH)
    login = (cfg.get("BATC_AGENT_LOGIN") or "").strip()
    if not login:
        raise SystemExit("BATC_AGENT_LOGIN is not set in .env")
    with _client() as client:
        user = _request(client, "GET", f"/users/{_sip_login(login)}")
    _print_json(user)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="show users, numbers and domain limits")
    info.set_defaults(func=cmd_info)

    create = sub.add_parser("create-agent", help="create a dedicated AI agent employee")
    create.add_argument("--force", action="store_true", help="create another employee")
    create.set_defaults(func=cmd_create_agent)

    agent = sub.add_parser("agent", help="show the configured agent employee")
    agent.set_defaults(func=cmd_agent)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
