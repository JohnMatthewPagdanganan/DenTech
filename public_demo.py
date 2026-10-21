"""Start/stop an isolated public DenTech demo through Cloudflare Quick Tunnel."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DEMO = ROOT / '.public-demo'
STATE = DEMO / 'state.json'
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def environment(url, secret, debug=False):
    env = os.environ.copy()
    env.update(DJANGO_SETTINGS_MODULE='config.public_settings', DJANGO_DEBUG='1' if debug else '0',
        DJANGO_SECRET_KEY=secret, PUBLIC_BASE_URL=url,
        DJANGO_ALLOWED_HOSTS=urlsplit(url).hostname + ',localhost,127.0.0.1')
    return env


def start_background(args, env, log):
    with log.open('ab') as stream:
        return subprocess.Popen(args, cwd=ROOT, env=env, stdout=stream, stderr=stream,
            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)


def stop_pid(pid, expected):
    if os.name != 'nt':
        raise RuntimeError('This launcher is configured for Windows.')
    # Check command identity before touching a PID saved by a previous run.
    result = subprocess.run(['powershell', '-NoProfile', '-Command',
        f'(Get-CimInstance Win32_Process -Filter "ProcessId = {int(pid)}").CommandLine'],
        capture_output=True, text=True, creationflags=NO_WINDOW)
    if expected.lower() in result.stdout.lower():
        subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'], capture_output=True, creationflags=NO_WINDOW)


def stop():
    if not STATE.exists():
        print('No saved public demo is running.')
        return
    state = json.loads(STATE.read_text())
    stop_pid(state['tunnel_pid'], state['cloudflared'])
    stop_pid(state['server_pid'], str(Path(__file__).resolve()))
    STATE.unlink()
    print('Public demo stopped. Its public URL no longer works. Local development server is unchanged.')


def serve():
    from config.wsgi import application
    from waitress import serve as waitress_serve
    waitress_serve(application, host='127.0.0.1', port=8011, threads=4,
        trusted_proxy='127.0.0.1', trusted_proxy_count=1,
        trusted_proxy_headers={'x-forwarded-proto', 'x-forwarded-for'},
        clear_untrusted_proxy_headers=True, max_request_body_size=12 * 1024 * 1024)


def start(cloudflared):
    cloudflared = Path(cloudflared).resolve(strict=True)
    DEMO.mkdir(exist_ok=True)
    if STATE.exists():
        raise RuntimeError('A saved public demo already exists. Run stop before starting a replacement.')
    tunnel = None
    server = None
    try:
        tunnel_log = DEMO / f'tunnel-{int(time.time())}.log'
        tunnel = start_background([str(cloudflared), 'tunnel', '--no-autoupdate', '--protocol', 'http2',
            '--url', 'http://127.0.0.1:8011'], os.environ.copy(), tunnel_log)
        print('Requesting a temporary Cloudflare HTTPS address...', flush=True)
        deadline = time.monotonic() + 75
        url = None
        while time.monotonic() < deadline:
            if tunnel.poll() is not None:
                raise RuntimeError(f'Tunnel exited. See {tunnel_log}')
            content = tunnel_log.read_text(errors='replace')
            match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', content)
            if match:
                url = match.group(0)
                break
            time.sleep(.5)
        if not url:
            raise RuntimeError(f'No public address returned. See {tunnel_log}')
        secret_file = DEMO / 'secret.txt'
        if not secret_file.exists():
            secret_file.write_text(secrets.token_urlsafe(64))
        secret = secret_file.read_text().strip()
        setup_env = environment(url, secret, debug=True)
        for command in [['migrate', '--noinput'], ['collectstatic', '--noinput']]:
            subprocess.run([sys.executable, str(ROOT / 'manage.py'), *command], cwd=ROOT, env=setup_env, check=True)
        seeded = subprocess.run([sys.executable, str(ROOT / 'manage.py'), 'seed_demo'], cwd=ROOT,
            env=setup_env, capture_output=True, text=True, check=True)
        if 'One-time generated demo password' in seeded.stdout:
            (DEMO / 'access.txt').write_text(seeded.stdout)
        server_log = DEMO / 'server.log'
        server = start_background([sys.executable, '-u', str(Path(__file__).resolve()), '_serve'],
            environment(url, secret), server_log)
        ready = False
        for _ in range(30):
            if server.poll() is not None:
                raise RuntimeError(f'Web server exited. See {server_log}')
            try:
                request = Request('http://127.0.0.1:8011/', headers={
                    'Host': urlsplit(url).hostname, 'X-Forwarded-Proto': 'https'})
                with urlopen(request, timeout=2) as response:
                    ready = response.status == 200
                if ready:
                    break
            except OSError:
                time.sleep(.5)
        if not ready:
            raise RuntimeError('Local public-demo web server did not become ready.')
        state = {'url': url, 'server_pid': server.pid, 'tunnel_pid': tunnel.pid,
                 'cloudflared': str(cloudflared), 'server_log': str(server_log), 'tunnel_log': str(tunnel_log)}
        STATE.write_text(json.dumps(state, indent=2))
        (DEMO / 'url.txt').write_text(url)
        print(f'PUBLIC_URL={url}', flush=True)
        print('Separate fictional demo database. DEBUG is off. Keep the laptop awake and connected.', flush=True)
        print(f'Account credentials: {DEMO / "access.txt"}', flush=True)
    except BaseException:
        if server:
            stop_pid(server.pid, str(Path(__file__).resolve()))
        if tunnel:
            stop_pid(tunnel.pid, str(cloudflared))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'stop', '_serve'])
    parser.add_argument('--cloudflared')
    args = parser.parse_args()
    if args.action == '_serve':
        serve()
    elif args.action == 'stop':
        stop()
    else:
        if not args.cloudflared:
            parser.error('start requires --cloudflared PATH_TO_OFFICIAL_CLOUDFLARED_EXE')
        start(args.cloudflared)
