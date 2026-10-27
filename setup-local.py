#!/usr/bin/env python3
"""Local development configuration; registration never rewrites existing secrets."""
import argparse
import json
import secrets
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
ENV = ROOT / '.env'
CONFIG = ROOT / 'collector' / 'config.json'
KEYS = ('POSTGRES_PASSWORD', 'DOCTOR_PASSWORD', 'JWT_SECRET')


def load_env(path):
    if not path.is_file():
        raise SystemExit(f'Missing {path}. Use --init for a NEW development setup.')
    result = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        result[key.strip()] = val
    return result


def placeholder(value):
    return not value or any(s in value.lower() for s in ('replace', 'change-me', 'development', 'your-password', 'example', 'paste-'))


def init():
    # Only create missing files; never rotate credentials on an existing installation.
    if ENV.exists():
        print(f'Existing {ENV} preserved. --init does not rotate credentials.')
        env = load_env(ENV)
    else:
        env = load_env(ROOT / '.env.example')
        for key in KEYS:
            if placeholder(env.get(key, '')):
                env[key] = secrets.token_urlsafe(32)
        env['DATABASE_URL'] = 'postgresql+psycopg://{}:{}@db:5432/{}'.format(
            quote(env.get('POSTGRES_USER', 'hospital_app'), safe=''),
            quote(env['POSTGRES_PASSWORD'], safe=''),
            quote(env.get('POSTGRES_DB', 'hospital_dashboard'), safe=''))
        env['ALLOWED_ORIGINS'] = 'http://localhost,http://localhost:8080,http://127.0.0.1:8080'
        ENV.write_text('\n'.join(f'{k}={v}' for k, v in env.items()) + '\n', encoding='utf-8')
        print('Created new .env. If an old PostgreSQL volume already exists, its password will NOT change.')

    if CONFIG.exists():
        print(f'Existing {CONFIG} preserved.')
    else:
        example = ROOT / 'collector' / 'config.example.json'
        cfg = json.loads(example.read_text(encoding='utf-8'))
        cfg['server_url'] = 'http://localhost:8080'
        CONFIG.write_text(json.dumps(cfg, indent=2) + '\n', encoding='utf-8')
        print('Created collector/config.json template for localhost; issue a per-machine collector credential before use.')


def inspect():
    env = load_env(ENV)
    if not CONFIG.is_file():
        raise SystemExit(f'Missing {CONFIG}. Use --init for a NEW development setup.')
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    required = ('DOCTOR_USERNAME', 'DOCTOR_PASSWORD')
    absent = [k for k in required if not env.get(k)]
    if absent:
        raise SystemExit('Missing environment settings: ' + ', '.join(absent))
    if placeholder(cfg.get('collector_key', '')):
        raise SystemExit('Collector credential is not provisioned. Issue a per-machine credential from the administrator API.')
    if not cfg.get('machine_code') or not cfg.get('server_url'):
        raise SystemExit('Collector machine_code or server_url is missing.')
    print('Configuration validated without modification.')
    print('Machine:', cfg['machine_code'])
    print('Server:', cfg['server_url'])
    print('Watched folders:', ', '.join(cfg.get('watch_folders', [])))
    print('Doctor username:', env['DOCTOR_USERNAME'])
    print('Credentials: not displayed or rotated.')
    return env, cfg


def request(url, method='GET', payload=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=15) as response:
        return json.load(response)


def register(env, cfg):
    base = cfg['server_url'].rstrip('/')
    try:
        print('Health:', request(base + '/api/health')['status'])
        auth = request(base + '/api/auth/login', 'POST', {
            'username': env['DOCTOR_USERNAME'], 'password': env['DOCTOR_PASSWORD']})
        machines = request(base + '/api/machines', token=auth['access_token'])
        if any(m.get('code') == cfg['machine_code'] for m in machines):
            print('Machine already registered:', cfg['machine_code'])
            return
        created = request(base + '/api/machines', 'POST', {
            'code': cfg['machine_code'],
            'name': 'CSO Sirius (local test)',
            'modality': cfg.get('study_title', 'Pupillography'),
            'location': 'Local testing',
        }, auth['access_token'])
        print('Registered machine:', created['code'])
    except (HTTPError, URLError, KeyError, ValueError) as error:
        raise SystemExit(f'Registration failed (configuration untouched): {error}') from error


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init', action='store_true', help='Create missing local config only; never overwrite existing config')
    parser.add_argument('--register', action='store_true', help='Register configured machine; never rewrite .env or config.json')
    args = parser.parse_args()
    if args.init:
        init()
    env, cfg = inspect()
    if args.register:
        register(env, cfg)
