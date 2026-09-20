"""Admin CLI.

Usage:
    python -m backend.cli create-admin --email admin@swarajsolar.com \
        --password <password> --name "Platform Admin"
    python -m backend.cli load-knowledge
"""

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    admin = sub.add_parser("create-admin", help="Create the initial super-admin user")
    admin.add_argument("--email", required=True)
    admin.add_argument("--password", required=True)
    admin.add_argument("--name", default="Platform Admin")

    sub.add_parser("load-knowledge", help="Load knowledge/ markdown into the database")

    args = parser.parse_args()
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
