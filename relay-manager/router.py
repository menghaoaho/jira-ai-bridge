#!/usr/bin/env python3
"""Publish only authorized, freshly verified device ports to nginx.

Runs as root solely because nginx reload requires it. Does not execute any
command or path from the database. The legacy backend remains a backup.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time

TARGET = Path('/etc/nginx/conf.d/jira-relay-pool.conf')
DATABASE = Path('/var/lib/jira-relay/relay.db')


def render(ports):
    checked = sorted(set(ports))
    if any(type(port) is not int or not 18120 <= port <= 18199 for port in checked):
        raise ValueError('port outside managed pool')
    lines = ['# Managed by jira-relay-router; legacy path remains a backup.', 'upstream jira_managed_relays {', '    least_conn;']
    lines += [f'    server 127.0.0.1:{port} max_fails=1 fail_timeout=10s;' for port in checked]
    lines += ['    server 127.0.0.1:8082' + (' backup;' if checked else ';'), '}',
              'server {', '    listen 127.0.0.1:18112;', '    server_name _;',
              '    access_log off;', '    client_max_body_size 2g;', '    location / {',
              '        proxy_pass http://jira_managed_relays;', '        proxy_http_version 1.1;',
              '        proxy_set_header Upgrade $http_upgrade;', '        proxy_set_header Connection $http_connection;', '        proxy_set_header Host $host;',
              '        proxy_set_header Origin $http_origin;', '        proxy_set_header Referer $http_referer;',
              '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
              '        proxy_connect_timeout 3s;', '        proxy_read_timeout 300s;', '        proxy_send_timeout 300s;',
              '        proxy_buffering off;', '        proxy_request_buffering off;',
              '        proxy_next_upstream error timeout http_502 http_503 http_504;',
              '        proxy_next_upstream_tries 3;', '    }', '}', '']
    return '\n'.join(lines)


def healthy_ports(database=DATABASE):
    now = time.time()
    with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True, timeout=3) as db:
        return [r[0] for r in db.execute('SELECT port FROM devices WHERE enabled=1 AND health_ok=1 AND health_at>? AND last_seen>?',
                                         (now - 20, now - 30))]


def publish(ports, target=TARGET):
    content = render(ports).encode()
    previous = target.read_bytes() if target.exists() else None
    if content == previous:
        return False
    temp = target.with_suffix('.tmp')
    temp.write_bytes(content); temp.chmod(0o644); os.replace(temp, target)
    try:
        subprocess.run(['nginx', '-t'], check=True, capture_output=True, timeout=10)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True, capture_output=True, timeout=15)
    except BaseException:
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            temp.write_bytes(previous); os.replace(temp, target)
        raise
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    lock = open('/run/jira-relay-router.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    while True:
        try:
            ports = healthy_ports()
        except (OSError, sqlite3.Error):
            # A management failure removes all new devices; the known legacy
            # path can keep existing usage working without unauthorized joins.
            ports = []
        try:
            publish(ports)
        except Exception:
            print('relay_route_update_failed', flush=True)
        if args.once:
            break
        time.sleep(5)


if __name__ == '__main__':
    main()
