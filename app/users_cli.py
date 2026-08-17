"""Account management CLI — the only way accounts are created.

Run inside the deployed container so it hits the real database:
  docker compose -f docker_compose_projects.yaml exec solitaire-web \
      python -m app.users_cli <add|list|passwd|role|disable|enable|remove> ...

Passwords are always prompted, never CLI arguments.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .users import ROLE_USER, ROLES, UserStore


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat: ") != password:
        sys.exit("passwords do not match")
    return password


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="app.users_cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("username")
    p_add.add_argument("--role", choices=ROLES, default=ROLE_USER)
    p_add.add_argument("--display-name", default="")

    sub.add_parser("list")

    for name in ("passwd", "disable", "enable", "remove"):
        sub.add_parser(name).add_argument("username")

    p_role = sub.add_parser("role")
    p_role.add_argument("username")
    p_role.add_argument("role", choices=ROLES)

    args = parser.parse_args(argv)
    store = UserStore()

    if args.cmd == "add":
        user = store.add(args.username, _prompt_password(), role=args.role, display_name=args.display_name)
        print(f"added {user.username} ({user.role})")
    elif args.cmd == "list":
        for user in store.list():
            flags = " disabled" if user.disabled else ""
            print(f"{user.username:24} {user.role:6}{flags}")
    elif args.cmd == "passwd":
        store.set_password(args.username, _prompt_password())
        print("password updated (existing sessions revoked)")
    elif args.cmd == "role":
        store.set_role(args.username, args.role)
        print("role updated")
    elif args.cmd == "disable":
        store.set_disabled(args.username, True)
        print("disabled (sessions revoked)")
    elif args.cmd == "enable":
        store.set_disabled(args.username, False)
        print("enabled")
    elif args.cmd == "remove":
        store.remove(args.username)
        print("removed")


if __name__ == "__main__":
    main()
