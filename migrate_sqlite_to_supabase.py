"""Copy existing Art House data from SQLite into an initialized Supabase DB."""
import os
import sqlite3
import sys
from pathlib import Path


TABLES = (
    'users', 'registered_accounts', 'account_credentials', 'sellers',
    'listed_items', 'checkouts', 'checkout_items', 'login_events',
)
IDENTITY_TABLES = ('users', 'registered_accounts', 'listed_items', 'checkouts', 'login_events')


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python migrate_sqlite_to_supabase.py path/to/auth.sqlite')
    database_url = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
    if not database_url:
        raise SystemExit('Set DATABASE_URL to the Supabase PostgreSQL connection string first.')
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as error:
        raise SystemExit('Install requirements.txt before running this migration.') from error

    source_path = Path(sys.argv[1])
    with sqlite3.connect(source_path) as source, psycopg.connect(
            database_url, row_factory=dict_row, sslmode='require') as destination:
        source.row_factory = sqlite3.Row
        existing = {row['name'] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        with destination.transaction():
            migration = (Path(__file__).parent / 'migrations' / '001_initial.sql').read_text(encoding='utf-8')
            for statement in migration.split(';'):
                if statement.strip():
                    destination.execute(statement)
            for table in TABLES:
                if table not in existing:
                    continue
                rows = source.execute(f'SELECT * FROM {table}').fetchall()
                if not rows:
                    continue
                columns = rows[0].keys()
                column_sql = ', '.join(f'"{column}"' for column in columns)
                placeholders = ', '.join('%s' for _ in columns)
                sql = f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
                with destination.cursor() as cursor:
                    cursor.executemany(sql, [tuple(row[column] for column in columns) for row in rows])
            for table in IDENTITY_TABLES:
                destination.execute(f'''SELECT setval(pg_get_serial_sequence(%s, 'id'),
                    GREATEST(COALESCE((SELECT MAX(id) FROM "{table}"), 1), 1),
                    EXISTS (SELECT 1 FROM "{table}"))''', (table,))
    print('SQLite accounts and catalog/order data copied to Supabase. Users will need to sign in again.')


if __name__ == '__main__':
    main()
