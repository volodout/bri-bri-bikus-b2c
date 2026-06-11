from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg

from app.config import settings


async def apply_migrations() -> None:
    connection = await asyncpg.connect(dsn=settings.database_url)
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )

        migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
        for migration_path in sorted(migrations_dir.glob("*.sql")):
            version = migration_path.name
            already_applied = await connection.fetchval(
                "SELECT 1 FROM schema_migrations WHERE version = $1",
                version,
            )
            if already_applied:
                continue
            async with connection.transaction():
                await connection.execute(migration_path.read_text(encoding="utf-8"))
                await connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)",
                    version,
                )
            print(f"applied {version}")
    finally:
        await connection.close()


def main() -> None:
    asyncio.run(apply_migrations())


if __name__ == "__main__":
    main()
