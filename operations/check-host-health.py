#!/usr/bin/env python3
import datetime,json,pathlib,shutil,subprocess
failures=[]
mem={line.split(':')[0]:int(line.split()[1]) for line in pathlib.Path('/proc/meminfo').read_text().splitlines()}
if mem['MemAvailable']/mem['MemTotal']<0.10:failures.append('available memory below 10%')
usage=shutil.disk_usage('/')
if usage.free/usage.total<0.15:failures.append('root disk free space below 15%')
backup=pathlib.Path('/var/backups/homelab/latest')
if not backup.exists():failures.append('no successful backup')
else:
 age=datetime.datetime.now().timestamp()-backup.resolve().stat().st_mtime
 if age>27*3600:failures.append('backup older than 27 hours')
pods=json.loads(subprocess.check_output(['kubectl','get','pods','-A','-o','json']))['items']
for pod in pods:
 m=pod['metadata'];s=pod['status']
 if m.get('deletionTimestamp') or s.get('phase')=='Succeeded':continue
 created=datetime.datetime.fromisoformat(m['creationTimestamp'].replace('Z','+00:00'))
 if (datetime.datetime.now(datetime.timezone.utc)-created).total_seconds()<300:continue
 if not any(c['type']=='Ready' and c['status']=='True' for c in s.get('conditions',[])):
  failures.append('Pod not ready: '+m['namespace']+'/'+m['name'])
apps=json.loads(subprocess.check_output(['kubectl','get','applications','-A','-o','json']))['items']
for app in apps:
 if app.get('status',{}).get('health',{}).get('status') in ['Degraded','Missing','Unknown']:
  failures.append('Argo CD unhealthy: '+app['metadata']['name'])
print('available_memory_percent',round(mem['MemAvailable']/mem['MemTotal']*100,1))
print('disk_free_percent',round(usage.free/usage.total*100,1))
for item in failures:print('FAIL',item)
if failures:raise SystemExit(1)
print('HOST_HEALTH_OK')
