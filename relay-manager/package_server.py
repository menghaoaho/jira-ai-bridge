#!/usr/bin/env python3
"""Package only allowlisted release files. Never include local state/keys."""
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent
FILES = ['service.py','auth.py','router.py','deploy.py','README.md','VALIDATION.md',
         'jira-relay.service','jira-relay-frps.service','jira-relay-router.service',
         'windows/install.ps1','web/index.html','web/app.js','web/style.css','web/login.html',
         'release/bundle.zip','release/frps','release/release.json']


def main():
    review = 'review.local.json' if (ROOT/'review.local.json').exists() else 'review.example.json'
    files = [*FILES, review]
    target = ROOT/'release/jira-relay-server-1.0.0.tar.gz'
    with tarfile.open(target,'w:gz') as archive:
        for name in files:
            archive.add(ROOT/name,arcname='jira-relay-release/'+name,recursive=False)
    result={'file':target.name,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size}
    (ROOT/'release/server-package.json').write_text(json.dumps(result,indent=2)+'\n')
    command = '''set -eu
python3 - <<'PY'
import datetime as dt
import hashlib
from pathlib import Path
import subprocess
import tarfile

archive = Path('/root/jira-relay-server-1.0.0.tar.gz')
expected = '@PACKAGE_SHA@'
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit('安装包摘要不一致，未执行安装。')
allowed = @ALLOWED_FILES@
destination = Path('/root/jira-relay-deploy-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
destination.mkdir(mode=0o700)
with tarfile.open(archive, 'r:gz') as package:
    members = package.getmembers()
    if len(members) != len(allowed) or {member.name for member in members} != set(allowed) or any(not member.isfile() for member in members):
        raise SystemExit('安装包文件清单不一致，未执行安装。')
    for member in members:
        target = destination / member.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(package.extractfile(member).read())
        target.chmod(0o755 if target.name == 'frps' else 0o644)
root = destination / 'jira-relay-release'
subprocess.run(['/usr/bin/python3', str(root / 'deploy.py'), '--review', str(root / '@REVIEW_FILE@')], check=True)
PY
'''.replace('@PACKAGE_SHA@', result['sha256']).replace('@ALLOWED_FILES@', repr(['jira-relay-release/'+x for x in files])).replace('@REVIEW_FILE@', review)
    (ROOT/'release/deployment-command.txt').write_text(command)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
