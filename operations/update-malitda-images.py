#!/usr/bin/env python3
"""검증된 CI만 발행하는 production 태그를 불변 digest로 Git에 기록한다."""
import base64,json,os,pathlib,re,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
# 폴더(역할) 이름과 저장소 이름이 다르다 — 웹은 개인 조직 규칙대로 -web.
IMAGES={'backend':'malitda/malitda-backend','frontend':'malitda/malitda-web'}
def digest(component,tag='production'):
    repo=IMAGES[component]
    # malitda-web은 비공개 패키지라 read:packages PAT(GHCR_READ_TOKEN)로 토큰을 받는다. 공개 패키지는 익명으로도 된다.
    auth={'Authorization':'Basic '+base64.b64encode(('seonwooj0810:'+os.environ['GHCR_READ_TOKEN']).encode()).decode()} if os.environ.get('GHCR_READ_TOKEN') else {}
    with urllib.request.urlopen(urllib.request.Request('https://ghcr.io/token?scope=repository:'+repo+':pull',headers=auth),timeout=20) as r:token=json.load(r)['token']
    request=urllib.request.Request('https://ghcr.io/v2/'+repo+'/manifests/'+tag,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'})
    with urllib.request.urlopen(request,timeout=20) as r:result=r.headers['Docker-Content-Digest']
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',result or ''):raise ValueError('invalid image digest')
    return result
if __name__=='__main__':
    # 양쪽 조회가 모두 성공한 뒤에만 파일을 수정한다.
    versions={component:digest(component) for component in IMAGES}
    for component,version in versions.items():
        path=ROOT/'apps'/'malitda'/component/'kustomization.yaml'
        text=path.read_text()
        updated,count=re.subn(r'    digest: sha256:[0-9a-f]{64}', '    digest: '+version,text)
        if count!=1:raise ValueError('expected exactly one image in '+str(path))
        path.write_text(updated)
        print(component,version)
