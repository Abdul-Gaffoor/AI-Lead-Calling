"""Admin CLI.

Usage:
    python -m backend.cli create-admin --email admin@swarajsolar.com \
        --password <password> --name "Platform Admin"
    python -m backend.cli load-knowledge
    python -m backend.cli evaluate [--all] [--json report.json]
"""

import argparse
import pathlib
import sys

from sqlalchemy import select

from backend.core.database import Base, SessionLocal, engine
from backend.core.security import hash_password
from backend.main import app  # noqa: F401  (imports all models)
from backend.auth.models import Role, User


def create_admin(email: str, password: str, name: str) -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == email.lower().strip()))
        if existing:
            print(f"User {email} already exists (role={existing.role.value})")
            return
        user = User(
            email=email.lower().strip(),
            full_name=name,
            hashed_password=hash_password(password),
            role=Role.SUPER_ADMIN,
        )
        db.add(user)
        db.commit()
        print(f"Created SUPER_ADMIN user {email}")


def load_knowledge() -> None:
    """Load `knowledge/` into the database as unapproved documents.

    Nothing becomes retrievable here: MVP section 18 only puts approved company
    information in front of a customer, so each document still has to be
    approved by a curator afterwards.
    """
    from backend.knowledge.loader import load_directory

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        result = load_directory(db)
        db.commit()
    print(
        f"Read {result['files']} files: {result['created']} new, "
        f"{result['updated']} updated, {result['failed']} failed."
    )
    if result["created"] or result["updated"]:
        print("All documents are UNAPPROVED and will not be used on calls "
              "until a curator approves them.")


def evaluate(deterministic_only: bool, json_path: str | None) -> None:
    """Run the MVP §37 corpus and print the §38 measures."""
    import json

    from backend.evaluation import build, load, render, run, to_dict
    from backend.evaluation.sandbox import session_factory

    scenarios = load()
    # Never the configured database: the harness writes call records.
    results = run(session_factory(), scenarios, deterministic_only=deterministic_only)
    report = build(results)

    print(render(report))

    if json_path:
        pathlib.Path(json_path).write_text(
            json.dumps(to_dict(report), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  Written to {json_path}")

    # A missed opt-out is a compliance breach, so it fails the command.
    # Everything else is a measurement, not a verdict.
    if not report.compliance_clean or report.errors:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(prog="backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    admin = sub.add_parser("create-admin", help="Create the initial super-admin user")
    admin.add_argument("--email", required=True)
    admin.add_argument("--password", required=True)
    admin.add_argument("--name", default="Platform Admin")

    sub.add_parser("load-knowledge", help="Load knowledge/ markdown into the database")

    evaluation = sub.add_parser(
        "evaluate", help="Run the Telugu evaluation corpus (MVP §37)"
    )
    evaluation.add_argument(
        "--all",
        action="store_true",
        help="Include model-graded scenarios (needs a real LLM provider; costs money)",
    )
    evaluation.add_argument("--json", dest="json_path", help="Also write the report here")

    args = parser.parse_args()
    if args.command == "evaluate":
        evaluate(deterministic_only=not args.all, json_path=args.json_path)
        return
    if args.command == "load-knowledge":
        load_knowledge()
        return
    if args.command == "create-admin":
        if len(args.password) < 8:
            print("Password must be at least 8 characters", file=sys.stderr)
            raise SystemExit(1)
        create_admin(args.email, args.password, args.name)


if __name__ == "__main__":
    main()
