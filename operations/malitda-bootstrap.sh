#!/usr/bin/env bash
# k3s 노드에서 실행하는 malitda 네임스페이스 부트스트랩·상태 확인용.
#
# 배포는 하지 않는다. 이미지는 각 앱 레포의 CI가 GHCR에 발행하고, 그 digest를
# operations/update-malitda-images.py가 apps/malitda/*/kustomization.yaml에 기록하며,
# 실제 클러스터 반영은 Argo CD가 한다. 이 스크립트는 클러스터를 처음 준비할 때
# (네임스페이스·시크릿이 갖춰졌는지) 또는 현재 상태만 훑어볼 때 쓴다.
#
# 예전에는 이 스크립트가 로컬 빌드와 매니페스트 apply까지 했는데, CI 경로와 이 경로가
# 같은 Deployment의 image 필드를 서로 모른 채 덮어써 나중에 실행된 쪽이 이기는 문제가
# 있었다(실제로 겪었다). 그래서 적용 책임은 Argo CD 한 곳으로 모았다.
set -euo pipefail

HOST=malitda.geonganghaejim.site
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== 사전 점검 =="
kubectl get ns malitda >/dev/null 2>&1 || kubectl apply -f "$ROOT/apps/malitda/backend/00-namespace.yaml"

# 시크릿 3종은 apps/malitda/backend/secrets/*.sealed.json 을 sealed-secrets 컨트롤러가
# 복호화해 만든다. 없다면 컨트롤러가 없거나, Argo CD가 아직 동기화하지 않았거나,
# 실링에 쓴 키가 이 클러스터의 것이 아니라는 뜻이다.
for s in malitda-azure malitda-postgres malitda-gemini; do
	if ! kubectl -n malitda get secret "$s" >/dev/null 2>&1; then
		echo "중단: 시크릿 $s 가 없다." >&2
		echo "  확인: kubectl get pods -n sealed-secrets-system" >&2
		echo "        kubectl -n malitda get sealedsecret" >&2
		echo "        argocd app get malitda-backend   (Synced 인지)" >&2
		exit 1
	fi
done

echo "== 상태 =="
kubectl -n malitda get pods,svc,ingress
kubectl -n malitda get certificate 2>/dev/null || true
echo
echo "URL: https://$HOST"
