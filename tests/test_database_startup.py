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
