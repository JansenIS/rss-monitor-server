from __future__ import annotations
import argparse
import json
from pathlib import Path
from app.db import SessionLocal, init_db
from app.importer import import_sources_payload


def main() -> None:
    parser = argparse.ArgumentParser(description='Import RSS sources JSON into Local Media Monitor RSS Server DB')
    parser.add_argument('json_file', type=Path)
    parser.add_argument('--include-secondary', action='store_true')
    args = parser.parse_args()

    init_db()
    payload = json.loads(args.json_file.read_text(encoding='utf-8-sig'))
    with SessionLocal() as db:
        result = import_sources_payload(db, payload, include_secondary=args.include_secondary)
    print(result.model_dump_json(indent=2))


if __name__ == '__main__':
    main()
