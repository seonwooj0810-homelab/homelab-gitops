# 홈서버 관측 스택 설계

작성: 2026-09-22 23:09 KST

## 1. 배경

이 클러스터에는 관측 인프라가 없다. 레포 전체에서 `prometheus|grafana|loki|metrics` 검색 결과가 0건이고,
`operations/check-host-health.py`는 메모리·디스크 퍼센트를 한 번 출력하는 단발 체크라 시계열이 남지 않는다.
알림 경로는 GitHub Actions 실패 표시가 전부다.

결과적으로 지금은 다음 질문에 답할 수단이 없다.

- Pod가 어젯밤에 왜 죽었는가 (재시작하면 `kubectl logs`가 증발한다)
- 디스크가 언제 찰 것인가
- 메모리 압박이 언제부터 시작됐는가

### 1.1 실측으로 확인한 현황 (2026-09-22)

| 항목 | 값 |
| --- | --- |
| 하드웨어 | Lenovo IdeaPad S340-15API, 8 코어 |
| 메모리 | 총 17GB, **가용 12GB** (현재 노드 사용률 26%) |
| 디스크 | NVMe 233GB, **여유 129GB** (42% 사용) |
| 스왑 | 8GB (702MB 사용) |
| k3s | v1.35.5+k3s1 |
| Argo CD | v3.4.2 |
| StorageClass | `local-path` (기본), `ALLOWVOLUMEEXPANSION=false` |
| 현재 전체 Pod 메모리 | 약 1.9GB |

이미 있는 것 (설계 시 중복 도입하지 않는다):

- **metrics-server** — k3s 기본 탑재, `kube-system`. `kubectl top` 동작함. 단 순간값만 제공하고 시계열 저장소가 아니다.
- **argocd-notifications-controller** — 실행 중이나 `argocd-notifications-cm`이 `argocdUrl: https://argocd.example.com`
  한 줄뿐인 빈 상태. 설치가 아니라 **설정만** 필요하다.
- **Argo CD UI 공개 노출** — `https://argocd.geonganghaejim.site` 응답 200, Ingress 121일째 가동, `admin.enabled: true`.
  `nginx.ingress.kubernetes.io/*` 어노테이션 + `ingressClassName: nginx` 패턴을 쓴다.

### 1.2 설계를 제약하는 사실

1. **PVC 확장 불가** — `local-path`의 `ALLOWVOLUMEEXPANSION=false`. 크기를 나중에 못 늘린다.
2. **product 네임스페이스가 default-deny** — `apps/*/resource-policy.yaml`의 `project-ingress`가
   `podSelector: {}` + `policyTypes: [Ingress]`이고, 허용 대상은 같은 네임스페이스 Pod와 `ingress-nginx`
   네임스페이스의 8080/3000뿐이다. 네임스페이스 **안으로** 스크랩하려면 규칙 추가가 필요하다(3단계에서 다룬다).
3. **앱 메트릭은 크로스 레포 작업** — 실측 결과 `tobehealthy/backend`는 actuator 자체가 없고(`/actuator/health` 404),
   `malitda/backend`는 actuator는 있으나 `/actuator/prometheus`가 404다. 노출하려면 `to-be-healthy/*`·`malitda/*`
   레포에 PR이 필요하다.
4. **Traefik은 마이그레이션 중** — `platform/traefik-values.yaml`이 `maxSurge: 0`이고, 롤백용 NGINX release가
   "upgrade 금지" 상태로 남아 있다. 관측 도입과 ingress 전환을 한 변경에 묶지 않는다.
5. **journald 무제한** — 현재 2.3GB, `/etc/systemd/journald.conf`에 상한 설정이 없다.

## 2. 목표와 비목표

### 목표

1. **사후 장애 원인 추적** — Pod 재시작 이후에도 죽기 전 로그를 조회할 수 있다.
2. **실시간 이상 알림** — 임계값 초과 시 폰으로 푸시가 도착한다.
3. **추세·용량 계획** — 디스크·메모리 추이를 주 단위로 본다.

### 비목표

- **"노드가 통째로 갔다"의 감지.** 단일 노드라 이 스택은 자기가 관측하는 대상과 함께 죽는다. 클러스터 내부
  알림은 "안에서 뭔가 잘못됐다"까지만 답한다. 노드 소실은 기존 GitHub Actions 외부 가용성 체크와 dead man's
  switch의 몫이며, Alertmanager에게 이 역할을 시키지 않는다.
- **고가용성.** 단일 노드에서 관측 스택을 이중화하는 것은 의미가 없다.
- **앱 내부 메트릭 (0~2단계 범위 밖).** 3단계에서 별도로 다룬다.

## 3. 결정 사항

| 결정 | 선택 | 근거 |
| --- | --- | --- |
| 스택 | **kube-prometheus-stack + Loki + Alloy** | 검증된 alert rule 수십 개가 기본 탑재. 알림이 목표에 포함된 이상 규칙을 직접 쓰는 비용이 가장 크다. 가용 메모리 12GB라 메모리는 변별 요소가 아니다 |
| `platform/` 관리 주체 | **Argo CD로 승격** | README가 선언한 구조와 일치. 관측이 첫 Argo 관리 platform 컴포넌트가 된다 |
| 알림 수신처 | **ntfy** | 개인 홈서버용. 폰 푸시, 계정 불필요, 자체호스팅 가능. 회사 Slack에 개인 인프라 알림을 섞지 않는다 |
| Grafana 노출 | **공개 (`grafana.geonganghaejim.site`)** | Argo CD UI와 동일한 노출 수준. port-forward로만 볼 수 있는 대시보드는 실제로 보지 않게 되고, 안 보는 대시보드는 없는 것과 같다 |

### 3.1 채택하지 않은 대안

- **VictoriaMetrics 조합** (~700MB, 컴포넌트 적음) — alert rule과 대시보드를 전부 직접 써야 한다. 12GB 여유
  상황에서 900MB를 아끼려고 규칙을 처음부터 쓰는 것은 절약이 아니라 이월된 비용이다.
- **Netdata 단독** (~250MB, 즉시 대시보드) — GitOps 친화성이 낮고 로그 집계가 없으며 장기 보존·쿼리가 약하다.
  자원이 빠듯할 때 유효한 선택이나 실측 결과 그 전제가 성립하지 않는다.

## 4. 아키텍처

```
┌─ 노드 (Lenovo IdeaPad, k3s 단일 노드) ────────────────────────┐
│                                                               │
│  kubelet/cAdvisor ──┐                                         │
│  node-exporter ─────┼──> Prometheus ──> Alertmanager ──┐      │
│  kube-state-metrics ┘         │                        │      │
│                               │                        │      │
│  /var/log/pods ──> Alloy ──> Loki                      │      │
│                               │                        │      │
│                               └──> Grafana <───────────┘      │
│                                       │                       │
│  Argo CD ──> argocd-notifications ────┼───────────────────┐   │
└───────────────────────────────────────┼───────────────────┼───┘
                                        │                   │
                              grafana.geonganghaejim.site   │
                                                            ▼
                                                        ntfy (폰 푸시)
```

핵심: **메트릭 수집 경로가 product 네임스페이스를 통과하지 않는다.** Pod별 CPU/메모리는 노드의 kubelet cAdvisor에서
나오고, kube-state-metrics는 API 서버를 읽고, Alloy는 노드의 로그 파일을 읽는다. 따라서 0~2단계는 기존
NetworkPolicy를 수정하지 않는다.

## 5. GitOps 레이아웃

```
platform/
├── 50-clusterissuers.yaml            (기존 → Argo 관리로 편입)
├── 60-argocd-ingress.yaml            (기존 → Argo 관리로 편입, 주석 수정)
├── traefik-values.yaml               (수동 유지 — 마이그레이션 종료까지)
└── observability/
    ├── kube-prometheus-stack.values.yaml
    ├── loki.values.yaml
    ├── alloy.values.yaml
    ├── grafana-ingress.yaml
    ├── alertmanager-ntfy.yaml        (Alertmanager -> ntfy 릴레이, 8.1 참조)
    ├── rules/
    │   └── homelab-rules.yaml        (자체 PrometheusRule)
    └── secrets/
        ├── ntfy.sealed.yaml
        └── grafana-admin.sealed.yaml

operations/                           (호스트 변경 — Argo CD가 적용할 수 없다)
├── journald-homelab.conf             (SystemMaxUse=1G, 2단계)
└── backup-textfile-exporter.sh       (node-exporter textfile collector, 2단계)

bootstrap/
├── platform-core.yaml                (신규 → platform/ 의 50·60번)
├── obs-kube-prometheus-stack.yaml    (신규, multi-source)
├── obs-loki.yaml                     (신규, multi-source)
└── obs-alloy.yaml                    (신규, multi-source)
```

- Application 형식은 기존 `bootstrap/app-backend.yaml` 컨벤션을 그대로 따른다
  (`project: default`, `prune`/`selfHeal`, `CreateNamespace=true`, `ApplyOutOfSyncOnly=true`, retry 백오프).
- **관측 Application에는 `ServerSideApply=true`를 추가로 넣는다.** kube-prometheus-stack의 CRD는
  `last-applied-configuration` 애노테이션 크기 한도를 초과해서, `app-backend.yaml`의 syncOptions를 그대로
  복사하면 **첫 sync가 실패한다.** 이 차트의 알려진 함정이며 추가 비용은 없다.
- Argo CD v3.4.2이므로 multi-source Application(차트는 helm repo, values는 이 git repo의 `$values` 참조)을 쓴다.
- **차트 버전을 Application에 고정한다**: `kube-prometheus-stack 91.4.1`(appVersion v0.94.0, Alertmanager
  v0.34.0), `loki 7.4.0`(appVersion 3.6.14), `alloy 1.12.1`. Traefik이 "차트 버전이 README에만 존재하는"
  상태인 것을 반복하지 않는다.
- **Loki values에 `schemaConfig`를 반드시 명시한다.** 차트의 `templates/validate.yaml`이 `loki.schemaConfig`와
  `loki.structuredConfig.schema_config`가 모두 비어 있고 `useTestSchema: false`이면 `fail`로 렌더링을 거부한다.
  기본값이 `{}`라 **지정하지 않으면 배포 자체가 안 된다.** `store: tsdb` / `object_store: filesystem` /
  `schema: v13`으로 둔다. 같은 파일이 "SingleBinary replica를 1보다 크게 두려면 오브젝트 스토리지가 필요하다"고도
  검증하므로 replica는 1을 유지한다.

### 5.1 `platform/60-argocd-ingress.yaml` 주석 수정

현재 주석은 이렇게 적혀 있다.

```
# platform/의 다른 것들과 마찬가지로 Argo CD가 관리하지 않으므로
# (자기 자신을 배포할 수는 없다) 클러스터에 직접 apply 한다
```

괄호 안의 근거는 이 파일에 해당하지 않는다. Argo CD의 자기 관리에서 조심할 대상은 `argocd-server`·
`application-controller` 같은 **핵심 워크로드**이며(sync 도중 자기를 재시작시켜 reconcile 루프가 끊길 수 있다),
argocd-server를 가리키는 **Ingress 오브젝트는 평범한 리소스**다. 승격과 함께 이 주석을 정확한 내용으로 고친다.

### 5.2 기존 리소스 adopt 절차

`50-clusterissuers.yaml`·`60-argocd-ingress.yaml`은 이미 라이브다(ClusterIssuer 둘 다 `READY=True`,
Ingress 121일 가동). Argo가 흡수(adopt)하는 형태가 된다.

1. `platform-core` Application을 **`syncPolicy.automated` 없이** 먼저 만든다.
2. Argo UI에서 diff가 비어 있는지 눈으로 확인한다.
3. 비어 있으면 `automated: {prune: true, selfHeal: true}`를 켠다.

**diff가 비어 있지 않으면 진행하지 않고 원인을 먼저 규명한다.** 라이브 리소스와 git이 다른 상태에서 selfHeal을
켜면 Argo가 라이브 쪽을 덮어쓴다.

## 6. 컴포넌트와 자원

| 컴포넌트 | 요청 | 제한 | 역할 |
| --- | --- | --- | --- |
| Prometheus | 512Mi | 2Gi | 메트릭 저장·규칙 평가 |
| Alertmanager | 64Mi | 256Mi | 알림 라우팅·중복 억제 |
| Grafana | 128Mi | 512Mi | 대시보드 |
| node-exporter (DaemonSet) | 32Mi | 128Mi | 호스트 메트릭 |
| kube-state-metrics | 64Mi | 256Mi | k8s 오브젝트 상태 |
| Loki (SingleBinary) | 256Mi | 1Gi | 로그 저장 |
| Alloy (DaemonSet) | 128Mi | 512Mi | 로그 수집 |
| **합계** | **~1.2Gi** | **~4.6Gi** | 가용 12GB의 10~38% |

### 6.1 반드시 끄는 것

- **Prometheus Operator admission webhook** — 단일 노드에 기동 순서 의존성만 추가한다.
- **Thanos 사이드카** — 단일 노드에 무의미하다.
- **etcd / kube-scheduler / kube-controller-manager ServiceMonitor** — k3s는 이 컴포넌트들이 단일 프로세스
  안에 있어 별도 엔드포인트를 노출하지 않는다. **기본값 그대로 두면 영구 `DOWN` 타깃과 상시 발화 알림이 생긴다.**
  k3s + kube-prometheus-stack 조합의 대표적 함정이며, 이것 때문에 알림을 무시하게 되면 스택 전체가 무력화된다.
- **Loki microservices 모드** — `deploymentMode: SingleBinary` + filesystem을 쓴다.

## 7. 스토리지와 보존

`ALLOWVOLUMEEXPANSION=false`이므로 **처음부터 크게 잡고, 보존 한도를 PVC 크기보다 낮게 건다.** 한도를 낮게 걸어야
꽉 찼을 때 오래된 데이터부터 삭제되고, Pod가 확장 불가능한 볼륨 위에서 먹통이 되는 상황을 피한다.

| PVC | 크기 | 보존 한도 | 근거 |
| --- | --- | --- | --- |
| Prometheus | 20Gi | `retention: 30d`, `retentionSize: 14GB` | PVC의 70%. 초과 시 오래된 블록부터 삭제 |
| Loki | 30Gi | compactor `retention_period: 14d`, `retention_enabled: true` | 로그가 메트릭보다 빨리 찬다 |
| Grafana | 2Gi | — | 임시 대시보드·설정 |
| Alertmanager | 1Gi | — | silence 상태 |
| **합계** | **53Gi** | | 여유 129Gi 중. 76Gi 남음 |

- **reclaim policy는 `local-path` 기본값 `Delete`를 유지한다.** 기존 PV 4개는 `Retain`으로 바꿨으나 관측 데이터는
  의도적으로 소모성이다. 날아가도 재구축되며, `Retain`은 PVC 재연결 절차만 늘린다. 이는 의도된 비대칭이다.
- **journald 상한은 호스트 변경이라 Argo CD가 적용할 수 없다.** `/etc/systemd/journald.conf`(또는
  `/etc/systemd/journald.conf.d/`)에 `SystemMaxUse=1G`를 넣는 일이므로, 레포의 기존 관례대로 `operations/`에
  파일을 두고 **수동 적용 단계로 2단계 산출물에 명시한다.** 로그를 Loki로 보내면서 호스트에 무한정 쌓을 이유가
  없다. 현재 2.3GB이고 상한 설정이 없다.

## 8. 알림

### 8.1 수신처

ntfy 토픽 하나를 쓰고 심각도를 우선순위로 구분한다.

- `critical` → ntfy priority `urgent` (폰 방해금지 무시)
- `warning` → ntfy priority `default`

토픽 이름은 추측 가능하면 누구나 구독·발행할 수 있으므로 **충분히 긴 무작위 문자열**을 쓰고
**SealedSecret으로 커밋한다** (`platform/observability/secrets/ntfy.sealed.yaml`). 레포의 기존 시크릿 관례와 동일하다.

**연동 방식 — ntfy용 네이티브 연동은 argocd-notifications에도 Alertmanager에도 없다.** 양쪽 다 범용 webhook으로
붙여야 하며, 붙이는 모양이 서로 다르다.

| 발신자 | 방식 | 본문 |
| --- | --- | --- |
| argocd-notifications | `service.webhook.ntfy` | **본문 템플릿을 자유롭게 쓸 수 있다.** 읽기 좋은 한 줄로 직접 구성 |
| Alertmanager | `webhook_configs` | **본문이 Alertmanager 고유 JSON으로 고정된다.** ntfy의 publish 포맷이 아니다 |

Alertmanager 쪽이 문제다. ntfy는 POST 본문을 그대로 알림 메시지로 삼으므로, 그냥 붙이면 **폰에 원시 JSON이 뜬다.**
알림을 실제로 읽게 만드는 것이 이 스택의 목적이므로 그대로 두지 않는다. 두 가지 선택지가 있다.

- **(채택) 릴레이를 둔다** — `alexbakker/alertmanager-ntfy`(★101, 최종 갱신 2026-03)를 observability 네임스페이스에
  Deployment 하나로 띄운다. 알림을 읽을 수 있는 문장으로 변환하고 심각도→priority 매핑도 이 안에서 한다.
  비용은 컨테이너 1개(~30Mi)와 서드파티 의존 하나다. **이미지는 digest로 고정**하고 Renovate 대상에 넣는다.
- (대안) **릴레이 없이 헤더만** — Alertmanager v0.34.0은 `http_config.http_headers`를 지원하므로(v0.25.0부터),
  `severity`로 라우팅한 **두 개의 webhook receiver**에 각각 정적 `Priority: urgent` / `Priority: default` 헤더를
  달면 우선순위 구분은 된다. 다만 본문은 원시 JSON 그대로다. 서드파티를 붙이기 싫을 때의 폴백.

**검증 항목**: 릴레이를 쓰든 안 쓰든 **폰에 도착한 알림이 사람이 읽을 수 있는 형태인지**를 11절에서 눈으로 확인한다.
4xx로 조용히 실패하거나 JSON 덩어리가 오는 것은 통과가 아니다.

### 8.2 argocd-notifications (0단계)

컨트롤러는 이미 실행 중이므로 설정만 한다.

- `argocdUrl`을 `https://argocd.example.com`에서 `https://argocd.geonganghaejim.site`로 교체
- 트리거: `on-health-degraded`, `on-sync-failed` 활성화. `on-sync-succeeded`는 **끈다**(이미지 bump마다 알림이
  오면 알림 피로로 전부 무시하게 된다)
- 구독은 Application 애노테이션이 아니라 `subscriptions` 기본값으로 전체 Application에 적용

### 8.3 Alertmanager (1단계)

**원칙: 차트 기본 규칙과 같은 것을 다시 쓰지 않는다.** 같은 조건의 규칙이 둘 있으면 알림이 두 번 오고, 그러면
둘 다 무시하게 된다. 기본 세트에 있는 것은 **임계값만 조정**한다.

기본 세트에 이미 있어 새로 쓰지 않는 것 (구현 시 차트의 규칙 목록으로 실물 확인할 것):

- `KubePersistentVolumeFillingUp` — PVC 포화. **확장이 불가능한 이 클러스터에서는 임계값을 기본보다 보수적으로
  낮추고 심각도를 올린다.** 새 규칙을 쓰는 대신 이걸 조정한다
- `NodeFilesystemSpaceFillingUp` — 루트 파티션 포화 예측. 4시간 변형이 기본 세트에 이미 있다
- `KubePodCrashLooping`, `KubeContainerWaiting`, `KubeNodeNotReady`, OOMKill 관련 규칙

**새로 쓰는 자체 규칙은 하나뿐이다.**

| 규칙 | 조건 | 심각도 | 메트릭 출처 |
| --- | --- | --- | --- |
| `BackupStale` | 최근 성공 백업 27시간 초과 | critical | **아래 plumbing 필요** |

`BackupStale`에는 메트릭 출처가 없다. 백업은 호스트의 systemd 타이머가 `/var/backups/homelab/`에 쓰는 것이라
클러스터 안에서는 보이지 않는다. 필요한 것:

- node-exporter에 `--collector.textfile.directory` 활성화 + hostPath 마운트
- `operations/backup-textfile-exporter.sh`가 최근 성공 백업의 UNIX timestamp를 `.prom` 파일로 기록
- `homelab-backup.service`의 `ExecStopPost`에서 호출

이 plumbing은 **2단계(호스트 변경을 모으는 단계)로 미룬다.** 1단계에서는 백업 신선도를 기존 host-health GHA 체크에
계속 맡긴다 — 이미 27시간 기준으로 검사하고 있으므로 공백이 생기지 않는다. plumbing을 넣지 않기로 하면 이 규칙은
**쓰지 않는다**(기존 체크와 중복이므로).

## 9. 접근 경로

`grafana.geonganghaejim.site`로 공개한다. **`platform/60-argocd-ingress.yaml`과 동일한 패턴**을 쓴다
(`cert-manager.io/cluster-issuer: letsencrypt-prod`, `nginx.ingress.kubernetes.io/*` 어노테이션,
`ingressClassName: nginx`). Traefik의 CRD provider는 꺼져 있으므로 `middlewares.traefik.io`는 쓰지 않는다.

보호 수단:

- Grafana 자체 로그인. **admin 비밀번호는 차트 기본값을 쓰지 않고 SealedSecret으로 주입한다.**
- 익명 열람(`auth.anonymous.enabled`) 비활성화.
- Prometheus·Alertmanager UI는 **Ingress를 만들지 않는다.** Grafana만 노출한다. 필요하면 port-forward.

## 10. 단계

| 단계 | 범위 | 산출물 | 목표 달성 |
| --- | --- | --- | --- |
| **0** | argocd-notifications 설정 + ntfy 토픽 | `argocd-notifications-cm` 패치, `ntfy.sealed.yaml` | 배포 실패 알림 |
| **1** | `platform/` Argo 승격 + kube-prometheus-stack + ntfy 릴레이 | `bootstrap/platform-core.yaml`, `obs-kube-prometheus-stack.yaml`, values, 임계값 조정, Grafana Ingress, `alertmanager-ntfy.yaml` | **실시간 알림 + 추세** |
| **2** | Loki + Alloy + 호스트 변경 | `obs-loki.yaml`, `obs-alloy.yaml`, values(`schemaConfig` 포함), `operations/journald-homelab.conf`, `operations/backup-textfile-exporter.sh` + `BackupStale` 규칙 | **사후 원인 추적** |
| **3** | 앱·DB 메트릭 (선택) | NetworkPolicy 수정, exporter, 앱 레포 PR | 앱 내부 가시성 |

0단계는 나머지와 독립이며 자원 소모가 없으므로 먼저 단독으로 내보낸다.

**2단계에 호스트 변경(journald 상한, textfile collector)을 모아둔 이유**: Argo CD가 적용할 수 없는 변경이라
수동 apply 단계가 필요하고, 이런 것이 여러 단계에 흩어지면 "git에는 있는데 호스트에 적용 안 된" 상태를 만들기 쉽다.
한 단계에 모아 한 번에 적용하고 확인한다.

### 10.1 3단계가 필요로 하는 것 (지금 하지 않음)

- `apps/tobehealthy/base/resource-policy.yaml`·`apps/malitda/backend/resource-policy.yaml`의 `project-ingress`에
  `observability` 네임스페이스 허용 규칙 추가
- mysqld-exporter / redis-exporter / postgres-exporter 사이드카 또는 Deployment
- `to-be-healthy/backend`에 actuator 추가, `malitda/backend`에 `micrometer-registry-prometheus` 및
  `management.endpoints.web.exposure.include`에 `prometheus` 추가 — **별도 레포 PR**

## 11. 검증

각 단계는 아래를 **실제로 실행해서** 확인하고 넘어간다. 매니페스트가 적용됐다는 것과 관측이 동작한다는 것은 다르다.

**1단계**

1. Prometheus `/targets`에서 `DOWN` 타깃이 **0개**인지 눈으로 확인한다. 6.1에서 끈 항목이 실제로 꺼졌는지 확인하는
   유일한 방법이다.
2. Grafana에 `https://grafana.geonganghaejim.site`로 접속해 TLS 인증서가 발급됐고 admin 기본 비밀번호가
   **거부되는지** 확인한다.
3. 테스트 알림 규칙을 일부러 발화시켜 **폰에 ntfy 푸시가 실제로 도착하는지**, 그리고 **사람이 읽을 수 있는
   형태인지** 확인한다. 규칙이 존재하는 것과 알림이 도착하는 것은 별개이고, 도착하는 것과 읽히는 것도 별개다.
   원시 JSON 덩어리가 오면 통과가 아니다(8.1).
4. Alertmanager 로그에 webhook 4xx가 없는지 확인한다. ntfy 연동 실패는 **알림이 안 오는 것으로만 드러나서**
   평소에는 정상과 구별되지 않는다.

**2단계**

5. Pod를 하나 의도적으로 삭제한 뒤, **삭제 전 로그가 Loki에서 조회되는지** 확인한다. 이것이 2단계의 존재 이유
   전부다. Pod가 살아 있는 상태에서 로그가 보이는 것은 이 단계가 동작한다는 증거가 아니다.
6. `journalctl --disk-usage`가 1G 이하로 수렴하는지 확인한다(설정 후 즉시 줄지 않을 수 있다).
7. `BackupStale` plumbing을 넣었다면, textfile collector가 내보낸 메트릭이 **Prometheus에서 실제로 조회되는지**
   확인한다. 파일이 생성된 것과 스크랩된 것은 별개다.

**공통**

8. 며칠 뒤 PVC 사용률을 확인하고, 보존 한도가 실제로 오래된 데이터를 지우는지 확인한다.

**공허한 통과 금지**: "쿼리 결과가 비어 있지만 에러도 없음"은 통과가 아니다. NetworkPolicy가 조용히 차단하는
경우가 정확히 이렇게 보인다. 각 검증은 **비어 있지 않은 결과**를 확인해야 한다.

## 12. 리스크

| 리스크 | 영향 | 완화 |
| --- | --- | --- |
| k3s에서 etcd/scheduler ServiceMonitor 기본값 방치 | 상시 오탐 알림 → 알림 전체를 무시하게 됨 | 6.1에서 명시적으로 끄고, 검증 1에서 `DOWN` 0개 확인 |
| PVC 확장 불가 상태에서 과소 산정 | 재생성 외에 복구 수단 없음 | 7절 기준으로 크게 잡고 보존 한도를 PVC의 70%로 |
| `platform/` adopt 시 라이브와 git 불일치 | selfHeal이 라이브를 덮어씀 | 5.2 — automated 끄고 diff 확인 후 켠다 |
| Grafana 공개 노출 | 인증 우회 시 내부 메트릭 유출 | admin 비번 SealedSecret, 익명 열람 차단, Prometheus/Alertmanager는 비노출 |
| 관측 스택이 노드 자원을 잠식 | 서비스 성능 저하 | 제한 총합 4.6Gi로 상한. 가용 12GB 대비 여유 있음 |
| ntfy 연동이 조용히 실패(4xx) | 알림이 안 오는데 정상과 구별 안 됨 | 검증 3·4에서 실제 도착과 Alertmanager 로그를 확인 |
| `alertmanager-ntfy` 서드파티 의존 | 미유지보수·공급망 | 이미지 digest 고정, Renovate 대상. 실패 시 8.1의 헤더 폴백으로 전환 |
| 호스트 변경이 git에만 있고 미적용 | 설정이 있다고 착각 | 2단계에 모아 한 번에 apply하고 검증 6·7로 확인 |
| 스택이 관측 대상과 함께 죽음 | 정작 필요할 때 알림 없음 | **설계상 수용.** 노드 소실 감지는 기존 GHA 외부 체크와 dead man's switch의 몫 (2절 비목표) |

---

## 13. 실행 중 Ruling

실행하면서 설계와 어긋난 사실이 확인되면 여기에 기록한다. 근거와 "틀렸을 때 비용"을 같이 적는다.

### R1. Loki 차트 핀을 `7.4.0` → `7.3.0`으로 내린다 (2026-09-23 10:17 KST)

**사실**: `helm search repo grafana/loki --version 7.4.0` 결과가 `No results found`.
`--versions` 전체 목록의 최신이 **`7.3.0` (appVersion 3.6.12)** 이다. 설계가 적은 `7.4.0`(appVersion 3.6.14)은
존재하지 않는 버전이다.

**조치**: `7.3.0`으로 고정한다. SingleBinary + filesystem + `schema: v13` 전제는 7.x에서 동일하므로
5절의 나머지 판단(특히 `schemaConfig` 필수, replica 1 유지)은 그대로 유효하다.

**틀렸을 때 비용**: 낮음. 존재하지 않는 버전을 그대로 두면 첫 sync가 `chart not found`로 즉시 실패하므로
조용히 잘못될 여지가 없다. 반대로 7.3.0에 문제가 있으면 Application의 `targetRevision` 한 줄로 되돌린다.

### R2. `kubeProxy.enabled: false`를 6.1의 "반드시 끄는 것"에 추가한다 (2026-09-23 10:17 KST)

**사실**: 6.1은 etcd·scheduler·controller-manager만 적었으나 **kube-proxy가 빠져 있다.** k3s는 kube-proxy도
서버 프로세스에 내장되어 별도 스크랩 엔드포인트(`:10249`)를 노출하지 않는다. 기본값(`kubeProxy.enabled: true`)을
두면 영구 `DOWN` 타깃이 하나 남는다.

**조치**: values에 `kubeProxy.enabled: false`를 넣는다.

**틀렸을 때 비용**: 이것을 빠뜨리면 11절 검증 1("`DOWN` 타깃 0개")이 실패하고, 12절이 지목한
"상시 오탐 → 알림 전체를 무시" 경로로 정확히 들어간다. 6.1과 같은 급의 항목이다.

### R3. ntfy는 공개 인스턴스(`ntfy.sh`)를 쓴다 (2026-09-23 10:17 KST)

**사실**: 3절 결정표는 "자체호스팅 가능"이라 적었을 뿐 어느 쪽인지 고르지 않았다. 10절은 0단계를
"나머지와 독립이며 **자원 소모가 없으므로** 먼저 단독으로 내보낸다"고 규정한다. 자체호스팅은 0단계에
Deployment+PVC를 추가하므로 이 규정과 충돌한다.

**조치**: `ntfy.sh` 공개 인스턴스 + 충분히 긴 무작위 토픽. 8.1이 요구한 대로 토픽은 SealedSecret으로만 커밋한다.

**틀렸을 때 비용**: 중간. 토픽 이름이 유출되면 제3자가 알림을 구독(내용 열람)하거나 발행(가짜 알림)할 수 있다.
완화는 토픽 길이(32자 무작위)와 평문 커밋 금지. 이 레포는 **공개**이므로 후자가 특히 중요하다 —
argocd-notifications의 webhook URL에 토픽이 들어가므로 URL 자체를 `argocd-notifications-secret`에 넣고
ConfigMap에서는 `$var`로 참조한다.

### R4. `KubePersistentVolumeFillingUp`은 임계값을 못 바꾼다 — severity만 올린다 (2026-09-23 10:35 KST)

**사실**: 8.3절은 이 규칙을 "새 규칙을 쓰는 대신 임계값만 조정한다"고 했으나, 차트 91.4.1의
`templates/prometheus/rules-1.14/kubernetes-storage.yaml`을 실물로 확인한 결과 임계값
(`< 0.03` 즉시 / `< 0.15` + 4일 예측)이 템플릿에 하드코딩돼 있다. `customRules`로 열려 있는 것은
`for`와 `severity` 둘뿐이고, 게다가 **두 변형이 같은 키를 공유**해서 severity를 나눠 줄 수도 없다.

**조치**: 둘 다 `severity: critical`, `for: 5m`으로 올린다. 늘릴 수 없는 볼륨에서는 "4일 뒤에 참"도
긴급이므로 의미가 맞는다. 임계값을 낮추려면 기본 규칙을 끄고 자체 규칙을 쓰는 수밖에 없는데,
그러면 8.3절이 금지한 "같은 조건의 규칙이 둘" 상태가 된다. 하지 않는다.

**틀렸을 때 비용**: 낮음. 이 알림은 애초에 2차 방어선이다. 1차 방어선은 7절의
`retentionSize: 14GB`(PVC 20Gi의 70%)로, 알림과 무관하게 오래된 블록부터 지워 볼륨이 차는 것을 막는다.
알림이 늦게 와도 볼륨이 터지지 않는 구조다. 반대로 severity를 올린 탓에 시끄러우면 되돌리기는 한 줄이다.

### R5. `alertmanager-ntfy` 설정은 통째로 SealedSecret에 넣는다 (2026-09-23 10:35 KST)

**사실**: 릴레이 설정 파일에는 `ntfy.notification.topic`이 반드시 들어간다. 8.1절이 토픽을
SealedSecret으로만 커밋하라고 했으므로 설정 파일을 ConfigMap으로 둘 수 없다.

**조치**: 설정 전체를 `secrets/alertmanager-ntfy-config.sealed.yaml`로 봉인해 파일로 마운트한다.
봉인하면 리뷰가 불가능해지므로, 토픽만 `<TOPIC>`으로 가린 사본을
`platform/observability/alertmanager-ntfy.config.example.yaml`에 남기고 재봉인 절차를 그 파일 머리에 적는다.
이 사본은 적용 대상이 아니라서 `obs-extras` Application의 `exclude`에 `*.example.yaml`을 넣었다.

**틀렸을 때 비용**: 낮지만 운영 부담이 있다. 설정을 고칠 때마다 재봉인이 필요하고, 사본과 실제 봉인 내용이
어긋나도 자동으로 드러나지 않는다. 그래서 재봉인 명령을 사본 안에 함께 적어 둘이 갈라지지 않게 한다.

### R6. `admissionWebhooks`를 끄면 `prometheusOperator.tls`도 같이 꺼야 한다 (2026-09-23 10:40 KST)

**사실**: 6.1은 admission webhook만 끄라고 했는데, `prometheusOperator.tls.enabled`가 기본 `true`이고
**별개 스위치**다. 켜져 있으면 operator Deployment가 `kube-prometheus-stack-admission` Secret을
`tls-secret` 볼륨으로 마운트하는데, 그 Secret을 만드는 주체가 방금 끈 webhook patch job이다.
실측: operator Pod가 `ContainerCreating`에서 3분 넘게 멈췄고 이벤트는
`MountVolume.SetUp failed for volume "tls-secret" : secret "kube-prometheus-stack-admission" not found`.

**조치**: `prometheusOperator.tls.enabled: false`를 함께 넣는다.

**틀렸을 때 비용**: 없음에 가깝다. 이 TLS는 webhook 서버용이고 webhook 자체를 쓰지 않는다.
빠뜨리면 operator가 아예 기동하지 못해 스택 전체가 올라오지 않으므로 조용히 잘못될 여지도 없다.

### R7. `alertmanager-ntfy` 플래그는 `--configs`다 (2026-09-23 10:40 KST)

**사실**: README 예시를 따라 `-config`로 주면 컨테이너가 기동 즉시
`unknown shorthand flag: 'c' in -config`로 종료한다. 실제 바이너리 `--help`로 확인한 플래그는
`--configs strings` (복수형, 기본값 `[config.yml]`)다.

**조치**: `args: ["--configs", "/etc/alertmanager-ntfy/config.yml"]`.

**틀렸을 때 비용**: 낮음. CrashLoopBackOff로 즉시 드러난다. 다만 **이 릴레이가 죽어 있으면
Alertmanager가 알림을 보내도 폰에는 아무것도 오지 않고, 그 상태가 "조용한 정상"과 구별되지 않는다**
(12절 리스크). 11절 검증 3·4를 반드시 실행해야 하는 이유가 이것이다.

### R8. Alloy가 오래된 로그를 보내면 **정상 로그까지 같이 버려진다** (2026-09-23 11:00 KST)

**사실**: 검증 5(삭제된 Pod의 로그가 Loki에 남는가)가 실패했다. Alloy 로그를 보면 대상 파일을
실제로 tail하고 있었는데도 Loki에는 아무것도 없었다. 원인은 Loki의 배치 거부다:

```
level=error msg="final error sending batch, no retries left, dropping data"
  status=400 ... has timestamp too old: 2026-08-30T19:38:58Z,
  oldest acceptable timestamp is: 2026-09-16T01:53:46Z
```

`loki.source.file`은 새로 발견한 파일을 **처음부터** 읽는다. 이 노드는 121일 가동 중이라
7월·8월자 컨테이너 로그가 남아 있고, Loki의 `reject_old_samples`(기본 7일)가 이를 거부한다.
문제는 거부가 **줄 단위가 아니라 배치 단위 400**이라는 점이다. 같은 배치에 실린 방금 생성된
로그까지 함께 버려진다.

**조치**: Alloy의 `loki.process`에 `stage.drop { older_than = "24h" }`를 넣어 애초에 보내지 않는다.
Loki 쪽 `reject_old_samples`를 끄는 선택지도 있으나, 그러면 121일치 과거 로그가 전부 적재되고
Loki의 방어선도 사라진다. 거르는 위치는 보내는 쪽이 맞다.

**틀렸을 때 비용**: 24시간보다 오래된 로그는 Loki에 들어오지 않는다. 이 스택의 목적이
"지금부터의 사후 추적"이므로 손실이 아니다. 다만 **도입 이전의 과거 로그는 조회할 수 없다** —
호스트의 `/var/log/pods`와 journald를 봐야 한다.

**이 건이 설계 11절 "공허한 통과 금지"의 실례다.** Alloy는 정상 기동했고, 에러 로그를 얕게 보면
`level=info start tailing file`만 보이며, Loki 쿼리는 에러 없이 빈 결과를 돌려줬다.
"비어 있지 않은 결과"를 요구하지 않았다면 통과로 처리됐을 상태다.
