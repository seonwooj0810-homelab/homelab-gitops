#!/usr/bin/env python3
"""서버 로컬 복구 지점을 만든다. 외부 복제 성공 전에는 원격 백업으로 간주하지 않는다."""
import datetime, gzip, hashlib, json, os, pathlib, shutil, sqlite3, subprocess, tarfile
os.umask(0o077)
base = pathlib.Path('/var/backups/homelab')
base.mkdir(mode=0o700, parents=True, exist_ok=True)
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
out = base / (stamp + '.partial')
out.mkdir(mode=0o700)
def save(name, args):
    with open(out / name, 'wb') as f:
        subprocess.run(args, stdout=f, check=True)
def kexec(ns, target, command):
    return ['kubectl', '-n', ns, 'exec', target, '--', 'sh', '-c', command]
try:
    save('malitda-postgres.dump', kexec('malitda', 'deploy/postgres', 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'))
    save('ttalkkak-postgres.dump', kexec('ttalkkak', 'deploy/postgres', 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc'))
    # 루트 비밀번호를 프로세스 인수나 로그에 넣지 않는다.
    save('mysql.sql', kexec('geonganghaegym', 'mysql-0', 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --all-databases --single-transaction --routines --events --triggers --no-tablespaces --set-gtid-purged=OFF'))
    with open(out/'mysql.sql','rb') as src, gzip.open(out/'mysql.sql.gz','wb') as dst: shutil.copyfileobj(src,dst)
    (out/'mysql.sql').unlink()
    save('redis.rdb', kexec('geonganghaegym', 'redis-0', 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli --rdb /tmp/homelab-backup.rdb >/dev/null 2>&1 && cat /tmp/homelab-backup.rdb && rm /tmp/homelab-backup.rdb'))
    volumes=json.loads(subprocess.check_output(['kubectl','get','pv','-o','json']))['items']
    files=next(v for v in volumes if v['spec'].get('claimRef',{}).get('namespace')=='geonganghaegym' and v['spec'].get('claimRef',{}).get('name')=='backend-files' and v['status']['phase']=='Bound')
    path=files['spec'].get('local',files['spec'].get('hostPath',{}))['path']
    with tarfile.open(out/'backend-files.tar.gz','w:gz') as tar:tar.add(path,arcname='backend-files')
    # SQLite online backup API는 WAL 쓰기 중에도 일관된 스냅샷을 생성한다.
    src=sqlite3.connect('file:/var/lib/rancher/k3s/server/db/state.db?mode=ro',uri=True)
    dst=sqlite3.connect(out/'k3s-state.db');src.backup(dst);dst.close();src.close()
    shutil.copyfile('/var/lib/rancher/k3s/server/token',out/'k3s-server-token')
    save('sealed-secrets-keys.json',['kubectl','-n','sealed-secrets-system','get','secret','-l','sealedsecrets.bitnami.com/sealed-secrets-key','-o','json'])
    save('cluster-secrets.json',['kubectl','get','secrets','-A','-o','json'])
    save('cluster-manifests.json',['kubectl','get','deploy,sts,ds,svc,ingress,pvc,pv,applications.argoproj.io,clusterissuers','-A','-o','json'])
    checks={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}
    (out/'checksums.json').write_text(json.dumps(checks,indent=2))
    final=base/stamp;out.rename(final)
    link=base/'latest.tmp';link.unlink(missing_ok=True);link.symlink_to(stamp);link.replace(base/'latest')
    # 보존 기간 14일. 실패한 백업은 기존 정상본을 삭제하지 않는다.
    for p in base.iterdir():
        if p.is_dir() and not p.is_symlink() and p.name.endswith('Z'):
            age=datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.strptime(p.name,'%Y%m%dT%H%M%SZ').replace(tzinfo=datetime.timezone.utc)
            if age.days>=14:shutil.rmtree(p)
    print('BACKUP_OK',final)
except Exception:
    print('BACKUP_FAILED',out)
    raise
