"""Entrypoint: apply migrations and reconcile the versioned app catalog."""

from app_dashboard.catalog import load_catalog, reconcile_catalog
from app_dashboard.config import get_settings
from app_dashboard.db import connect, run_migrations


def main() -> None:
    settings = get_settings()
    conn = connect()
    try:
        run_migrations(conn)
        reconcile_catalog(conn, load_catalog(settings.apps_config_path))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
