"""Entrypoint: apply migrations and reconcile the versioned app catalog."""

from app_dashboard.catalog import load_catalog, reconcile_catalog
from app_dashboard.config import get_settings
from app_dashboard.db import connect, run_migrations


def main() -> None:
    settings = get_settings()
    conn = connect()
    try:
        run_migrations(conn)
        # YAML is an import source, not a perpetual owner. Once the database
        # has a catalog, management edits must survive every deploy/restart.
        if conn.execute("select count(*) from apps").fetchone()[0] == 0:
            reconcile_catalog(conn, load_catalog(settings.apps_config_path))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
