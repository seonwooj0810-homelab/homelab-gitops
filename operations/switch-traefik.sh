#!/usr/bin/env bash
set -euo pipefail
# 기존 NodePort와 hostPort 두 경로를 모두 보존한다.
rollback() {
  echo 'Cutover failed; restoring ingress-nginx' >&2
  kubectl -n ingress-nginx patch ds traefik --type=merge -p '{"spec":{"template":{"spec":{"nodeSelector":{"homelab-disabled":"true"}}}}}' || true
  kubectl -n ingress-nginx delete pod -l app.kubernetes.io/name=traefik --wait=true --timeout=60s || true
  kubectl -n ingress-nginx patch ds ingress-nginx-controller --type=json -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector/homelab-disabled"}]' || true
  kubectl apply -f /var/backups/homelab/nginx-service.json || true
}
trap rollback ERR
kubectl -n ingress-nginx patch ds ingress-nginx-controller --type=merge -p '{"spec":{"template":{"spec":{"nodeSelector":{"homelab-disabled":"true"}}}}}'
kubectl -n ingress-nginx wait --for=delete pod -l app.kubernetes.io/name=ingress-nginx --timeout=90s
KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm upgrade traefik traefik --repo https://traefik.github.io/charts --version 41.6.0 -n ingress-nginx -f /tmp/traefik-values.yaml --wait --timeout 180s
python3 - <<'PY'
import subprocess,json
s=json.loads(subprocess.check_output(['kubectl','get','svc','traefik','-n','ingress-nginx','-o','json']))
p={'spec':{'selector':s['spec']['selector'],'ports':[{'name':'http','port':80,'protocol':'TCP','targetPort':'web','nodePort':31477},{'name':'https','port':443,'protocol':'TCP','targetPort':'websecure','nodePort':31735}]}}
subprocess.run(['kubectl','patch','svc','ingress-nginx-controller','-n','ingress-nginx','--type=merge','-p',json.dumps(p)],check=True)
PY
for host in malitda.geonganghaejim.site geonganghaejim.site argocd.geonganghaejim.site; do
  curl --fail --silent --show-error --max-time 15 --resolve "$host:443:192.168.0.2" "https://$host/" -o /dev/null
  echo "PASS $host"
done
trap - ERR
echo CUTOVER_OK
