"""Fail-closed VPS credentials and a small session-mode pool; no local fallback."""
import json
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

PROJECT = 'ojmokmsjppkqwppmrzpv'
HOST = 'aws-0-eu-north-1.pooler.supabase.com'


def credential_url(path, role):
    path = Path(path)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('Credential file must be private')
    data = json.loads(path.read_text())
    if (data['host'], data['port'], data['dbname'], data['user'], data['sslmode']) != (
            HOST, 5432, 'postgres', role + '.' + PROJECT, 'require'):
        raise ValueError('Unexpected Supabase session-pooler credential')
    return URL.create('postgresql+psycopg2', username=data['user'], password=data['password'],
                      host=data['host'], port=data['port'], database=data['dbname']), data


def private_engine(path, role):
    url, _ = credential_url(path, role)
    return create_engine(url, pool_size=1, max_overflow=0, pool_timeout=20,
                         pool_pre_ping=True, pool_recycle=300, hide_parameters=True,
                         connect_args={'sslmode': 'require', 'connect_timeout': 20,
                                       'application_name': role, 'keepalives_idle': 30,
                                       'keepalives_interval': 10, 'keepalives_count': 3})


class LockedSession(Session):
    """Never reconnect a pinned collection session after losing its advisory lock."""
    def __init__(self, *args, **kwargs):
        connection = kwargs['bind']
        if connection.closed or connection.invalidated:
            raise RuntimeError('Collection lock connection was lost; stop without replay')
        super().__init__(*args, **kwargs)


def verify_connection(engine, expected_role):
    with engine.connect() as c:
        role = c.execute(text('select current_user')).scalar()
        if role != expected_role:
            raise RuntimeError('Unexpected database role')
        return {'role': role, 'database': c.execute(text('select current_database()')).scalar(),
                'mode': 'session', 'tls_required': True}
