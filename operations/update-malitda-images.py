#!/usr/bin/env python3
"""검증된 CI만 발행하는 production 태그를 불변 digest로 Git에 기록한다."""
import json,pathlib,re,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
def digest(component,tag='production'):
    repo='malitda/malitda-'+component
    with urllib.request.urlopen('https://ghcr.io/token?scope=repository:'+repo+':pull',timeout=20) as r:token=json.load(r)['token']
    request=urllib.request.Request('https://ghcr.io/v2/'+repo+'/manifests/'+tag,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'})
    with urllib.request.urlopen(request,timeout=20) as r:result=r.headers['Docker-Content-Digest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',result or ''):raise ValueError('invalid image digest')
    return result
if __name__=='__main__':
    # 양쪽 조회가 모두 성공한 뒤에만 파일을 수정한다.
    versions={component:digest(component) for component in ['backend','frontend']}
    for component,version in versions.items():
        path=ROOT/'apps'/'malitda'/component/'kustomization.yaml'
        text=path.read_text()
        updated,count=re.subn(r'    digest: sha256:[0-9a-f]{64}', '    digest: '+version,text)
        if count!=1:raise ValueError('expected exactly one image in '+str(path))
        path.write_text(updated)
        print(component,version)
