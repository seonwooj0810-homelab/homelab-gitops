#!/usr/bin/env python3
"""전용 SSH 키에서 암호화 백업 전송/수신 확인만 허용한다."""
import datetime,json,pathlib,re,subprocess,sys
base=pathlib.Path('/var/backups/homelab')
command=sys.argv[1] if len(sys.argv)>1 else 'export'
if command=='export':
    source=(base/'latest').resolve(strict=True)
    if source.parent!=base or not re.fullmatch(r'\d{8}T\d{6}Z',source.name):raise SystemExit('invalid snapshot')
    tar=subprocess.Popen(['tar','-C',str(base),'-czf','-',source.name],stdout=subprocess.PIPE)
    enc=subprocess.run(['age','-R','/etc/homelab-backup/recipient.txt'],stdin=tar.stdout)
    tar.stdout.close()
    if tar.wait()!=0 or enc.returncode!=0:raise SystemExit(1)
elif re.fullmatch(r'ack \d{8}T\d{6}Z',command):
    snapshot=command.split()[1]
    if not (base/snapshot/'checksums.json').is_file():raise SystemExit('unknown snapshot')
    record={'snapshot':snapshot,'verified_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'destination':'user-mac'}
    (base/'offserver-receipt.json').write_text(json.dumps(record))
    (base/'offserver-receipt.json').chmod(0o600)
    print('RECEIPT_OK')
else:raise SystemExit('only export and ack are allowed')
