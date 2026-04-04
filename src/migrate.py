"""
One-time migration: import existing JSON files into SQLite.

Usage:
  python migrate.py
  python migrate.py --dry-run   # show what would be migrated without writing

Safe to run multiple times — duplicates are skipped.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import database as db
from config import AUTH_DB_PATH, DATABASE_PATH, MEMORY_DIR

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def preview(memory_dir: Path, auth_db_path: Path) -> None:
    """Show what would be migrated without writing anything."""
    print(f"Database target: {DATABASE_PATH}\n")

    if auth_db_path.exists():
        users = json.loads(auth_db_path.read_text())
        print(f"Users ({auth_db_path}):")
        for username, data in users.items():
            print(f"  • {username} ({data.get('display_name', username)})")
    else:
        print(f"No users file found at {auth_db_path}")

    print()

    if memory_dir.exists():
        for mem_file in sorted(memory_dir.glob("*.json")):
            data = json.loads(mem_file.read_text())
            user_id = mem_file.stem
            h = len(data.get("history", []))
            p = len(data.get("preferences", []))
            t = len(data.get("tool_knowledge", []))
            print(f"Memory for '{user_id}' ({mem_file}):")
            print(f"  • {h} history entries, {p} preferences, {t} tool knowledge facts")
    else:
        print(f"No memory directory found at {memory_dir}")


def main():
    parser = argparse.ArgumentParser(description="Migrate HearthForge JSON data to SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    if args.dry_run:
        preview(MEMORY_DIR, AUTH_DB_PATH)
        return

    db.init_db()
    counts = db.migrate_from_json(MEMORY_DIR, AUTH_DB_PATH)

    print(f"Migration complete → {DATABASE_PATH}")
    print(f"  Users:          {counts['users']}")
    print(f"  History:         {counts['history']}")
    print(f"  Preferences:     {counts['preferences']}")
    print(f"  Tool knowledge:  {counts['tool_knowledge']}")

    if not any(counts.values()):
        print("\nNothing to migrate (no JSON files found or already migrated).")


if __name__ == "__main__":
    main()
