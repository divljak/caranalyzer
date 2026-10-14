"""Capture hosted read evidence with existing Vercel authentication; no OLX visits."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

from verify_scheduled_collection import HOST, private_write


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def capture(directory, cli):
    os.umask(0o077)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    body_path, headers_path = directory / 'overview-body.json', directory / 'overview-headers.txt'
    # Header file may contain private cookies. Never print it; output only cache policy.
    subprocess.run([*cli, 'curl', 'https://' + HOST + '/api/overview', '--', '--silent',
                    '--show-error', '--fail', '--max-time', '45', '--dump-header', str(headers_path),
                    '--output', str(body_path)], check=True, capture_output=True, text=True)
    headers = headers_path.read_text().splitlines()
    statuses = [int(line.split()[1]) for line in headers if line.startswith('HTTP/')]
    cache = next((line.split(':',1)[1].strip() for line in headers
                  if line.lower().startswith('cache-control:')), '')
    captured = datetime.now(timezone.utc).isoformat()
    private_write(directory / 'overview.json', {'captured_at':captured, 'url':'https://' + HOST + '/api/overview',
                  'status':statuses[-1], 'cache_control':cache, 'body':json.loads(body_path.read_text())})
    opener = urllib.request.build_opener(NoRedirect())
    access = {'captured_at':captured, 'redirect_is_vercel_login':True}
    for label, path in (('html','/market'), ('api','/api/overview')):
        try:
            response = opener.open('https://' + HOST + path, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            access[label+'_status'] = response.code
            location = response.headers.get('Location', '')
            access['redirect_is_vercel_login'] &= location.startswith('https://vercel.com/')
    private_write(directory / 'access.json', access)
    print(json.dumps({'overview_status':statuses[-1], 'cache_control':cache, **access}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--vercel-js', type=Path, help='Use an already-installed modern CLI with node')
    args = parser.parse_args()
    try:
        capture(args.output_dir, ['node',str(args.vercel_js)] if args.vercel_js else ['vercel'])
    except Exception as exc:
        print(json.dumps({'result':'fail','failure_class':type(exc).__name__}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
