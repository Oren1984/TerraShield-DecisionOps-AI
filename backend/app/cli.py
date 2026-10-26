"""Operator command line: ``python -m app.cli <command>``."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.config import settings
from app.db import session_scope
from app.logging_setup import configure_logging


def cmd_seed(args: argparse.Namespace) -> None:
    from app.seed import seed_all

    with session_scope() as db:
        seed_all(db, demo=not args.no_demo)
    print("seed: reference data, corpus and demo runs are in place")


def cmd_create_owner(args: argparse.Namespace) -> None:
    from app.auth.passwords import hash_password, password_problems
    from app.models import User, Workspace, WorkspaceMember
    from app.services import seed_workspace_scenarios

    password = os.environ.get("TS_OWNER_PASSWORD") or getpass.getpass("Owner password: ")
    problems = password_problems(password, args.username)
    if problems:
        sys.exit("; ".join(problems))
    with session_scope() as db:
        if db.scalar(select(func.count()).select_from(User).where(User.role == "owner")):
            sys.exit("An owner already exists. Use the Users page to manage accounts.")
        user = User(username=args.username, password_hash=hash_password(password), role="owner")
        db.add(user)
        db.flush()
        ws = Workspace(name=args.workspace)
        db.add(ws)
        db.flush()
        db.add(WorkspaceMember(user_id=user.id, workspace_id=ws.id))
        seed_workspace_scenarios(db, ws.id, user.id)
    print(f"owner '{args.username}' created with workspace '{args.workspace}'")


def cmd_build_fixture_vectors(_: argparse.Namespace) -> None:
    from app.seed import FIXTURE_VECTORS, build_fixture_vectors, corpus_dir

    data = build_fixture_vectors()
    path = corpus_dir() / FIXTURE_VECTORS
    path.write_text(json.dumps(data, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(data['vectors'])} fixture vectors to {path}")


def cmd_embed(args: argparse.Namespace) -> None:
    from app.agents.retrieval import embed_chunks, register_ollama_model

    with session_scope() as db:
        model = register_ollama_model(db, args.model)
        n = embed_chunks(db, model.id)
    print(f"embedded {n} chunks with {model.id} (dimension {model.dimension}, version {model.version})")


def cmd_readiness(_: argparse.Namespace) -> None:
    from app.agents.providers import readiness

    print(json.dumps(readiness(), indent=2))


def cmd_scenario_schema(args: argparse.Namespace) -> None:
    from app.simulation.scenario import ScenarioParams

    out = json.dumps(ScenarioParams.model_json_schema(), indent=2)
    if args.output:
        Path(args.output).write_text(out + "\n", encoding="utf-8")
    else:
        print(out)


def main() -> None:
    configure_logging(settings.log_level)
    ap = argparse.ArgumentParser(prog="terrashield")
    sub = ap.add_subparsers(required=True)
    p = sub.add_parser("seed", help="seed reference data and demo runs (idempotent)")
    p.add_argument("--no-demo", action="store_true")
    p.set_defaults(fn=cmd_seed)
    p = sub.add_parser("create-owner", help="create the first owner (password from TS_OWNER_PASSWORD or prompt)")
    p.add_argument("--username", required=True)
    p.add_argument("--workspace", default="Harbor Vale Operations")
    p.set_defaults(fn=cmd_create_owner)
    p = sub.add_parser("build-fixture-vectors", help="regenerate the Mock-mode fixture vectors")
    p.set_defaults(fn=cmd_build_fixture_vectors)
    p = sub.add_parser("embed", help="(re-)embed the corpus with a local Ollama embedding model")
    p.add_argument("--model", default=None)
    p.set_defaults(fn=cmd_embed)
    p = sub.add_parser("readiness", help="check local model readiness")
    p.set_defaults(fn=cmd_readiness)
    p = sub.add_parser("scenario-schema", help="print the scenario parameter JSON schema")
    p.add_argument("--output")
    p.set_defaults(fn=cmd_scenario_schema)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
