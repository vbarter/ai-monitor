"""CLI: scan, digest, init-db."""

from __future__ import annotations

import argparse
import logging
import sys

from model_radar import __version__
from model_radar.config import load_config, resolve_config_path, resolve_db_path
from model_radar.db import connect
from model_radar.digest import run_digest
from model_radar.http import HttpClient
from model_radar.scan import run_scan

log = logging.getLogger("model_radar")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="model_radar")
    parser.add_argument("--version", action="version", version=f"model-radar {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="fetch sources and send immediate alerts")
    digest = sub.add_parser("digest", help="print or send the non-immediate backlog")
    init_db = sub.add_parser("init-db", help="create the sqlite schema")
    for command in (scan, digest, init_db):
        command.add_argument("--config", help="path to config.yaml (default: ./config.yaml)")
        command.add_argument("--db", help="sqlite path (overrides config and MODEL_RADAR_DB)")
        command.add_argument("--verbose", action="store_true")
    scan.add_argument("--only", action="append", default=[], help="source id to fetch; repeatable")
    digest.add_argument("--send", action="store_true", help="post the digest to Slack")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
        db_path = resolve_db_path(config, args.db)
        database = connect(db_path)
    except (OSError, ValueError) as exc:
        log.error("%s", exc)
        return 2

    if args.command == "init-db":
        log.info("database ready at %s", db_path)
        database.close()
        return 0

    http = HttpClient()
    try:
        if args.command == "scan":
            run_scan(config, database, http, only=set(args.only or []))
        elif args.command == "digest":
            run_digest(config, database, http, send=args.send)
        else:
            return 2
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
