"""Private Vercel runtime; collector and local database settings stay separate."""
import os
from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


def hosted_engine():
    value = os.environ.get('DATABASE_URL', '')
    if not value:
        raise RuntimeError('Hosted DATABASE_URL is required')
    url = make_url(value)
    if not (url.host or '').endswith('.pooler.supabase.com') or url.port != 6543:
        raise RuntimeError('Hosted database requires the Supabase transaction pooler')
    if url.username != 'car_analyzer_app.ojmokmsjppkqwppmrzpv':
        raise RuntimeError('Hosted database requires the restricted application role')
    if url.query.get('sslmode') != 'require':
        raise RuntimeError('Hosted database requires TLS')
    return create_engine(url, poolclass=NullPool, hide_parameters=True,
                         connect_args={'connect_timeout': 10})


def hosted_hosts():
    return list({value for value in (
        os.getenv('VERCEL_URL'), os.getenv('VERCEL_BRANCH_URL'),
        *os.getenv('APP_ALLOWED_HOSTS', '').split(','),
    ) if value})


def same_origin(origin, host, base_url, hosted):
    if hosted:
        parsed = urlsplit(origin or '')
        return parsed.scheme == 'https' and parsed.netloc == host
    return not origin or origin == base_url.rstrip('/')
