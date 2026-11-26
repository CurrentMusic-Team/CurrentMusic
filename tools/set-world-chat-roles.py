#!/usr/bin/env python3
"""Initialize world chat roles using two existing local login names."""
import argparse
from contextlib import closing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cm_db
import cm_social


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--admin", required=True)
    args = parser.parse_args()
    with closing(cm_db.connect()) as db:
        cm_social.init_tables(db)
    try:
        cm_social.seed_world_roles(args.owner, args.admin)
    except cm_social.Error as error:
        parser.exit(1, f"{error}\n")
    print("世界聊天群主和管理员已核验")


if __name__ == "__main__":
    main()
