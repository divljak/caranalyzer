"""Bootstrap synthetic integration fixtures in an explicitly disposable loopback DB."""
import argparse
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]


def bootstrap(port=55434, database='car_analyzer_integration'):
    if not database.startswith('car_analyzer_integration') or not 1024 < port < 65536:
        raise ValueError('Only a disposable integration database is allowed')
    engine = create_engine(URL.create('postgresql+psycopg2', username='postgres',
                                     host='127.0.0.1', port=port, database=database),
                           hide_parameters=True)
    try:
        with engine.begin() as c:
            if c.execute(text('SELECT count(*) FROM pg_tables WHERE schemaname=\'public\'')).scalar():
                raise ValueError('Refuse a nonempty database')
            c.exec_driver_sql('CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN;')
            paths = [
                'supabase/migrations/20261013132620_car_analyzer_baseline.sql',
                'supabase/migrations/20261013133615_website_checks_run_index.sql',
                'supabase/manual-history/20261013134056_private_vercel_runtime.sql',
                'supabase/manual-history/20261013164856_daily_collector_delivery.sql',
                'supabase/migrations/20261013174917_direct_collector_roles.sql',
                'supabase/migrations/20261014155031_collector_priority_read_access.sql',
                'deploy/backup/sequence-read.sql',
            ]
            for path in paths:
                with c.connection.driver_connection.cursor() as cursor:
                    cursor.execute((ROOT / path).read_text())
            c.execute(text("""INSERT INTO scraping_logs(session_id,start_time,end_time,
                status,errors_count) VALUES ('integration-fixture','2026-10-01 17:00:00',
                '2026-10-01 17:31:22','completed',0)"""))
            assert not c.execute(text("SELECT rolcanlogin FROM pg_roles WHERE rolname='car_analyzer_sync'")).scalar()
        print('Disposable schema, final role boundaries and synthetic clean anchor prepared')
    finally:
        engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=55434)
    parser.add_argument('--database', default='car_analyzer_integration')
    args = parser.parse_args()
    bootstrap(args.port, args.database)
