#!/usr/bin/env python3
"""암호문만 저장하고 복호화/파일 해시를 검증한 후 서버에 수신 확인한다."""
import datetime,fcntl,hashlib,json,os,pathlib,subprocess,tarfile
os.umask(0o077)
base=pathlib.Path.home()/'Library/Application Support/HomelabBackup'
archives=base/'archives';archives.mkdir(exist_ok=True)
lock=open(base/'pull.lock','w')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit(0)
ssh=['/usr/bin/ssh','-i',str(base/'pull-key'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','ubuntu@116.120.240.197']
partial=archives/'download.partial'
try:
    with open(partial,'wb') as f:subprocess.run(ssh+['export'],stdout=f,check=True,timeout=1800)
    decrypt=subprocess.Popen([str(base/'bin/age'),'-d','-i',str(base/'identity.agekey'),str(partial)],stdout=subprocess.PIPE)
    actual={};expected=None;snapshot=None
    with tarfile.open(fileobj=decrypt.stdout,mode='r|gz') as tar:
        for member in tar:
            parts=pathlib.PurePosixPath(member.name).parts
            if snapshot is None:snapshot=parts[0]
            if parts[0]!=snapshot or '..' in parts:raise ValueError('invalid archive path')
            if not member.isfile():continue
            name='/'.join(parts[1:]);stream=tar.extractfile(member)
            if name=='checksums.json':expected=json.load(stream)
            else:
                h=hashlib.sha256()
                while data:=stream.read(1024*1024):h.update(data)
                actual[name]=h.hexdigest()
    # age 인증 태그까지 읽는다. 평문은 디스크에 저장하지 않는다.
    while decrypt.stdout.read(1024*1024):pass
    if decrypt.wait()!=0 or expected is None or actual!=expected:raise ValueError('backup integrity check failed')
    destination=archives/(snapshot+'.tar.gz.age')
    partial.replace(destination)
    subprocess.run(ssh+['ack '+snapshot],check=True,timeout=30)
    (base/'last-success.json').write_text(json.dumps({'snapshot':snapshot,'verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'archive':str(destination)},indent=2))
    # 서버와 독립적으로 최근 암호화 완료본 30개를 보관한다.
    for old in sorted(archives.glob('*.tar.gz.age'))[:-30]:old.unlink()
    print('ENCRYPTED_BACKUP_VERIFIED',snapshot,flush=True)
except Exception:
    partial.unlink(missing_ok=True)
    raise
