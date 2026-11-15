"""Catch hosted database driver failures without opening a database connection."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1] / 'backend'


@pytest.mark.parametrize('scheme', ['postgresql', 'postgres', 'postgresql+psycopg2'])
def test_postgres_app_import_uses_installed_driver(scheme):
    env = dict(os.environ, DATABASE_URL=f'{scheme}://test:p%40ss%3Aword@localhost/test?sslmode=require')
    result = subprocess.run(
        [sys.executable, '-c', '''
import database
import main
assert database.engine.dialect.driver == 'psycopg2'
assert database.engine.dialect.dbapi.__name__ == 'psycopg2'
assert database.engine.url.password == 'p@ss:word'
assert database.engine.url.query['sslmode'] == 'require'
assert not database.is_sqlite
'''], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_sqlite_app_import_still_works():
    result = subprocess.run(
        [sys.executable, '-c', 'import database; import main; assert database.is_sqlite'],
        cwd=BACKEND, env=dict(os.environ, DATABASE_URL='sqlite:///:memory:'),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not os.getenv("FINREVIEW_TEST_POSTGRES_URL"), reason="requires disposable PostgreSQL")
def test_postgres_v2_startup_protects_tables_and_preserves_legacy_rows():
    result = subprocess.run(
        [sys.executable, '-c', '''
import os
import subprocess
import database
from sqlalchemy import create_engine, inspect, text
from database import engine, init_db
from models.portable_models import Holding, Transaction, User
with engine.begin() as connection:
    connection.execute(text("CREATE ROLE anon"))
    connection.execute(text("CREATE ROLE authenticated"))
    connection.execute(text(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated"
    ))
    User.__table__.create(connection)
    Holding.__table__.create(connection)
    Transaction.__table__.create(connection)
    for fixture in (
        User(id=1, email="fixture@example.com", password="fixture"),
        Holding(user_id=1, symbol="INFY.NS", quantity=3, avg_price=100, asset_type="STOCK"),
        Transaction(user_id=1, symbol="INFY.NS", quantity=5, price=100, type="BUY"),
        Transaction(user_id=1, symbol="INFY.NS", quantity=2, price=110, type="SELL"),
    ):
        connection.execute(fixture.__table__.insert().values(**fixture.model_dump(exclude_none=True)))

def legacy_snapshot(db_engine):
    with db_engine.connect() as connection:
        return {table.name: connection.execute(table.select().order_by(table.c.id)).all()
                for table in (User.__table__, Holding.__table__, Transaction.__table__)}

snapshot = legacy_snapshot(engine)
container = os.environ.get("FINREVIEW_TEST_POSTGRES_CONTAINER")
if container:
    # The CI service container supplies matching PostgreSQL client binaries.
    docker = ["docker", "exec", "-i", container]
    dump = subprocess.check_output([*docker, "pg_dump", "-U", "postgres", "-Fc", "finreview_ci"])
    subprocess.run([*docker, "createdb", "-U", "postgres", "finreview_restore"], check=True)
    subprocess.run([*docker, "pg_restore", "-U", "postgres", "--exit-on-error", "-d", "finreview_restore"],
                   input=dump, check=True)
    assert set(inspect(engine).get_table_names()) == {"user", "holding", "transaction"}
    database.engine = create_engine(engine.url.set(database="finreview_restore"))
    assert legacy_snapshot(database.engine) == snapshot
engine = database.engine
for _ in range(2):
    init_db()
with engine.connect() as connection:
    assert connection.execute(text('SELECT count(*) FROM "user"')).scalar_one() == 1
    tables = inspect(connection).get_table_names()
    assert len(tables) == 12
    protection = connection.execute(text("""
        SELECT relrowsecurity,
            has_table_privilege('anon', oid, 'SELECT'),
            has_table_privilege('authenticated', oid, 'TRUNCATE')
        FROM pg_class
        WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' AND relname NOT IN ('user', 'holding', 'transaction')
    """)).all()
    assert protection == [(True, False, False)] * 9
assert legacy_snapshot(engine) == snapshot
'''], cwd=BACKEND, env=dict(os.environ, DATABASE_URL=os.environ['FINREVIEW_TEST_POSTGRES_URL']),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
