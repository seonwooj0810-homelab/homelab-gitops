#!/usr/bin/env python3
"""운영 볼륨을 연결하지 않고 임시 Pod에 DB 백업을 복원한다."""
import gzip,json,pathlib,subprocess
ns='homelab-restore-check'
def run(args,**kw):return subprocess.run(['kubectl']+args,check=True,**kw)
def apply(obj):run(['apply','-f','-'],input=json.dumps(obj),text=True)
backup=pathlib.Path('/var/backups/homelab/latest').resolve()
apply({'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns}})
apply({'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':'deny-network','namespace':ns},'spec':{'podSelector':{},'policyTypes':['Ingress','Egress']}})
try:
 for name,image,env,args in [
  ('postgres','postgres:17-alpine',[{'name':'POSTGRES_HOST_AUTH_METHOD','value':'trust'}],['-c','listen_addresses=127.0.0.1']),
  ('mysql','mysql:8.0',[{'name':'MYSQL_ALLOW_EMPTY_PASSWORD','value':'yes'}],['--skip-networking'])]:
  command=['sh','-c','test "$(cat /proc/1/comm)" = postgres && pg_isready -U postgres'] if name=='postgres' else ['sh','-c','test "$(cat /proc/1/comm)" = mysqld && mysqladmin ping -uroot']
  apply({'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':ns},'spec':{'restartPolicy':'Never','automountServiceAccountToken':False,'containers':[{'name':name,'image':image,'env':env,'args':args,'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'1','memory':'2Gi'}},'readinessProbe':{'exec':{'command':command},'periodSeconds':3,'failureThreshold':60}}]}})
 run(['wait','--for=condition=Ready','pod','--all','-n',ns,'--timeout=180s'])
 run(['exec','-n',ns,'postgres','--','createdb','-U','postgres','restore_test'])
 with open(backup/'malitda-postgres.dump','rb') as f:
  run(['exec','-i','-n',ns,'postgres','--','pg_restore','--exit-on-error','--no-owner','-U','postgres','-d','restore_test'],stdin=f)
 run(['exec','-n',ns,'postgres','--','psql','-U','postgres','-d','restore_test','-Atc',"select count(*) from information_schema.tables where table_schema='public'"])
 with gzip.open(backup/'mysql.sql.gz','rb') as f:
  p=subprocess.Popen(['kubectl','exec','-i','-n',ns,'mysql','--','mysql','-uroot'],stdin=subprocess.PIPE)
  while data:=f.read(1024*1024):p.stdin.write(data)
  p.stdin.close()
  if p.wait()!=0:raise RuntimeError('MySQL restore failed')
 print('POSTGRES_MYSQL_RESTORE_OK')
finally:
 run(['delete','namespace',ns,'--wait=false'])
