# homelab-gitops

GitOps source-of-truth for the homelab k3s node. Argo CD watches this repo and applies changes.

**This repo is deliberately not owned by any single product.** It holds the cluster-wide platform
config, the node's operations tooling, and the per-product manifests for every app running on the
node. Application source code stays in each product's own org (`geonganghaegym/*`, `malitda/*`) —
only "how the cluster is deployed and operated" lives here.

## Layout

```
homelab-gitops/
├── apps/                       Per-product manifests. One Argo CD Application per leaf directory.
│   ├── malitda/
│   │   ├── backend/            namespace, postgres, backend, ingress, resource-policy, sealed secrets
│   │   └── frontend/
│   └── geonganghaegym/
│       ├── backend/            + pvc
│       ├── frontend/
│       ├── base/               namespace, mysql, redis, ingress, resource-policy
│       └── overlays/prod/      kustomize overlay — image digests, configs, sealed secrets
├── bootstrap/                  Argo CD Application / ApplicationSet definitions (kubectl apply'd once)
├── platform/                   Cluster-wide, product-agnostic: ClusterIssuers, Traefik values
├── operations/                 Node operations: backup, host/public health checks, systemd units
└── gha-templates/              Workflows to copy into the product app repos
```

New shared tooling that belongs to the node rather than to a product (monitoring, log collection,
…) goes under `platform/` — not into a product's `apps/` directory, and not into a separate repo.

## Flow

```
[push to geonganghaegym/geonganghaegym-backend or -web main]
        │
        ▼
[GHA: build → push image to ghcr.io/geonganghaegym/geonganghaegym-<backend|web>:sha-XXXX]
        │
        ▼
[GHA: checkout homelab-gitops, run `kustomize edit set image`, commit, push]
        │
        ▼
[Argo CD detects new commit → kustomize build overlays/prod → kubectl apply]
        │
        ▼
[Deployment rolling restart with the new image]
```

---

## One-time setup (perform on GitHub)

### 1. This repo

`seonwooj0810-homelab/homelab-gitops` (public). It was moved here from `to-be-healthy/k8s-manifests`
on 2026-09-22 — the old URL still resolves through GitHub's transfer redirect, but nothing should
rely on that. If you find a reference to the old name, fix it.

### 2. Create a Personal Access Token (PAT) for CI write access

GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens:
- Repository access: `seonwooj0810-homelab/homelab-gitops`
- Permissions: Contents (read/write)
- Copy the token.

**The PAT must be scoped to this repo's new org.** A token scoped to the old `to-be-healthy` org
keeps working for reads through the redirect but cannot push here — and the failure only surfaces
on the next deploy, in the manifest-bump step, after the image has already been published.

### 3. Add the PAT to both app repos as `MANIFEST_REPO_TOKEN`

For each of `geonganghaegym/geonganghaegym-backend` and `geonganghaegym/geonganghaegym-web`:
- Settings → Secrets and variables → Actions → New repository secret
- Name: `MANIFEST_REPO_TOKEN`
- Value: the PAT from step 2.

### 4. Add frontend build-time vars to `geonganghaegym/geonganghaegym-web`

Settings → Secrets and variables → Actions → **Variables** tab (not Secrets — these are baked into the JS bundle, so not secret):

- `NEXT_PUBLIC_WEB_URI` = `https://geonganghaejim.site`
- `NEXT_PUBLIC_API_URL` = `/api`
- `NEXT_PUBLIC_AUTH_URL` = `/api`
- `INTERNAL_API_URL` = `http://backend:8080`
- `NEXT_PUBLIC_KAKAO_CLIENT_ID` = `...`
- `NEXT_PUBLIC_NAVER_CLIENT_ID` = `...`
- `NEXT_PUBLIC_GOOGLE_CLIENT_ID` = `...`
- `NEXT_PUBLIC_APPLE_CLIENT_ID` = `com.geonganghaejim.signin`

### 5. Copy workflow files into the app repos

```bash
# in geonganghaegym/geonganghaegym-backend
mkdir -p .github/workflows
cp <this repo>/gha-templates/backend-deploy.yml .github/workflows/deploy.yml
git add .github/workflows/deploy.yml && git commit -m "ci: build & push to GHCR, bump manifest" && git push

# same in geonganghaegym/geonganghaegym-web
cp <this repo>/gha-templates/frontend-deploy.yml .github/workflows/deploy.yml
```

### 6. Make GHCR images public (or set up imagePullSecret)

By default ghcr.io images are private. Easiest: in GitHub org settings → Packages, mark `backend` and `frontend` packages as **public** after the first push.

Alternative for private images: create an imagePullSecret in the cluster:
```bash
kubectl -n geonganghaegym create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=<github-user> \
  --docker-password=<PAT with read:packages>
# then add `imagePullSecrets: [{name: ghcr-pull}]` to each Deployment spec.
```

---

## One-time setup (perform on the cluster)

### 1. Bootstrap secrets and ClusterIssuers (Argo CD does not manage these)

```bash
ssh ubuntu@116.120.240.197
cd ~/workspace/k8s-manifests

# ClusterIssuers (cluster-scoped, applied once)
kubectl apply -f platform/50-clusterissuers.yaml
```

Secrets are no longer applied from `.env` files — they are committed here as sealed-secrets
(`apps/*/*/secrets/*.sealed.*`) and decrypted in-cluster by the sealed-secrets controller.

### 2. Register the Argo CD Application

```bash
kubectl apply -f bootstrap/applicationset.yaml
# Argo CD will then pull this repo, kustomize-build overlays/prod, and apply.
```

### 3. (Optional) Access the Argo CD UI

```bash
# Get the initial admin password
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo

# Port-forward to localhost
kubectl -n argocd port-forward svc/argocd-server 8443:443
# Open https://localhost:8443  (user: admin)
```

Or expose via Ingress at `argocd.geonganghaejim.site` (add DNS A record first):
```bash
kubectl -n argocd apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: argocd
  namespace: argocd
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/backend-protocol: "HTTP"
spec:
  ingressClassName: nginx
  tls:
    - hosts: [argocd.geonganghaejim.site]
      secretName: argocd-tls
  rules:
    - host: argocd.geonganghaejim.site
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service: {name: argocd-server, port: {number: 80}}
EOF
```

---

## How a deploy happens end-to-end

1. Developer pushes a commit to `geonganghaegym/geonganghaegym-backend@main`.
2. `.github/workflows/deploy.yml`:
   - Builds image, tags with `sha-<short-commit>` and `latest`.
   - Pushes to `ghcr.io/geonganghaegym/geonganghaegym-backend`.
   - Checks out `seonwooj0810-homelab/homelab-gitops`, runs `kustomize edit set image` in `apps/geonganghaegym/backend`, commits, pushes.
3. Argo CD sees a new commit on the manifest repo:
   - Re-runs `kustomize build overlays/prod`.
   - Applies the diff (only the image tag changed → Deployment is patched).
   - Kubernetes rolling-updates the pod.

## Troubleshooting

- `argocd app sync geonganghaegym-backend` from the Argo CD CLI to force a sync.
- `kubectl -n argocd describe app geonganghaegym-backend` to see sync status / errors.
- `kubectl -n geonganghaegym describe pod -l app=backend` if a new image fails to pull.
- Check Argo CD UI → Application → "App Diff" tab for current vs desired state.

## Local emergency override

If Argo CD is down and you must hot-patch:
```bash
kubectl -n geonganghaegym set image deploy/backend backend=ghcr.io/geonganghaegym/geonganghaegym-backend:hotfix
```
**Note:** self-heal will revert this on the next sync if `automated.selfHeal: true`. Disable temporarily via UI if you need the manual override to stick.
