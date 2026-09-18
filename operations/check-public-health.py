#!/usr/bin/env python3
"""GitHub-hosted runner에서 실행: 노트북 전원/회선 장애도 감지한다."""
import socket, ssl, time, urllib.request
hosts=['malitda.geonganghaejim.site','geonganghaejim.site']
failures=[]
for host in hosts:
    error=None
    for attempt in range(3):
        try:
            with urllib.request.urlopen('https://'+host,timeout=15) as response:
                if response.status!=200:raise RuntimeError('HTTP '+str(response.status))
            with socket.create_connection((host,443),timeout=10) as sock:
                with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as tls:
                    expires=ssl.cert_time_to_seconds(tls.getpeercert()['notAfter'])
                    if expires-time.time()<7*86400:raise RuntimeError('certificate expires within 7 days')
            error=None;print('OK',host);break
        except Exception as exc:
            error=f'{host}: {type(exc).__name__}: {exc}'
            if attempt<2:time.sleep(10)
    if error:failures.append(error)
if failures:
    for failure in failures:print('::error::'+failure)
    raise SystemExit(1)
