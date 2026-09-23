# 단일 노트북 운영 절차

## 현재 배포 경로

- K3s 단일 노드, SQLite datastore. 노트북 장애 시 모든 서비스가 중단된다.
- Traefik chart `41.6.0` / image `v3.7.13`, namespace `ingress-nginx`.
- 기존 `nginx` IngressClass/annotations는 Traefik의 공식 NGINX 호환 provider가 처리한다.
- hostPort 80/443과 기존 NodePort 31477/31735를 모두 유지한다.
- `ingress-nginx-controller` DaemonSet은 nodeSelector로 비활성화했고 이전 Helm release는 롤백용으로 남겼다. **이 release를 upgrade하면 이전 컨트롤러가 다시 실행될 수 있으므로 실행하지 않는다.**
- 퇴역한 NGINX admission webhook은 제거했다. 기존 웹훅을 복원하려면 NGINX Pod가 먼저 정상이어야 한다.
- 단일 노드 hostPort 사용으로 Traefik 갱신은 `maxSurge: 0, maxUnavailable: 1`. 갱신 중 짧은 접속 중단이 가능하다.

## 말잇다 GitOps

1. 각 비공개 소스 저장소의 Actions가 테스트 후 GHCR에 SHA 태그와 `production` 태그를 발행한다.
2. 이 공개 운영 저장소의 `Update Malitda images`가 공개 이미지의 `production` digest를 조회한다.
3. digest 변경을 `apps/malitda/{backend,frontend}/kustomization.yaml`에 커밋한다.
4. Argo CD `malitda-backend`, `malitda-frontend`가 Git 변경을 적용한다.

소스 저장소에 대한 Argo CD 자격증명이나 개인 PAT는 필요 없다. 이미지와 운영 설정이 공개라는 현재 전제를 사용한다. 평문 Secret은 저장하지 않고 SealedSecret만 커밋한다.

이미지 점검 cron은 5분 간격이지만 GitHub 예약 실행은 지연될 수 있다. 긴급 배포는 이미지 발행 완료 후 `gh workflow run malitda-images.yml -R seonwooj0810-homelab/homelab-gitops`로 실행한다. CI 성공은 이미지 발행 성공이며 서비스 반영 완료는 Argo CD의 `Synced / Healthy`로 판단한다.

Kubernetes 설정은 **이 저장소의 apps/malitda**에서만 변경한다. 앱 저장소에 있던 `deploy/` 참고본은 2026-09-22에 삭제했다 — 내용이 이 저장소와 동일한 중복이라, 그쪽을 고치고 반영됐다고 오해할 여지가 실제 이득보다 컸다.

네임스페이스·시크릿이 갖춰졌는지 확인하고 현재 상태를 훑어보려면 노드에서 `operations/malitda-bootstrap.sh`를 실행한다(말잇다 backend 저장소의 `deploy/deploy.sh`가 여기로 옮겨온 것이다). 배포는 하지 않는다 — 적용 책임은 Argo CD 한 곳에 있다.

롤백할 때는 먼저 `Update Malitda images` 워크플로를 일시 중지하고, 이전 digest 커밋으로 되돌린 뒤 Argo CD를 확인한다. `production` 태그도 원하는 버전으로 재발행한 뒤 워크플로를 재개한다. 워크플로를 켜 둔 채 Git만 되돌리면 다음 점검에서 다시 최신 production digest로 바뀐다. DB 스키마는 이미지 롤백으로 되돌아가지 않으므로 호환성을 별도 확인한다.

## 백업

- 설치: `/usr/local/sbin/homelab-backup` (`backup.py`)
- 예약: `homelab-backup.timer`, 서버 시간 기준 매일 04:00 + 최대 10분 지연
- 보존: 완료본 14일, 권한 0700 디렉터리와 0600 파일
- 위치: `/var/backups/homelab/<UTC timestamp>/`, `latest` 심볼릭 링크
- 대상: PostgreSQL custom dump, MySQL 전체 논리 dump, Redis RDB, 업로드 파일, SQLite online backup, K3s server token, Sealed Secrets 키, Kubernetes Secret/주요 매니페스트
- SQLite와 서비스 DB 덤프는 각각 일관되게 생성하지만 전체 서비스의 동일 시점 트랜잭션 스냅샷은 아니다. 업로드 파일은 백업 중 변경 가능하다.
- 서버 외부 복제: 사용자 Mac의 `~/Library/Application Support/HomelabBackup/archives/`에 age 암호문을 보관한다.
- Mac LaunchAgent `kr.malitda.homelab-backup`가 로그인 시와 4시간 간격으로 전송한다. Mac이 꺼져 있거나 접속할 수 없으면 실행/전송되지 않는다. 별도 상시 클라우드 저장소나 다른 지역 백업은 아니다.
- `pull-backup.py`는 스트림 복호화 후 모든 파일의 SHA-256을 검증하고 서버에 수신 확인을 기록한다. 평문은 Mac 디스크에 저장하지 않는다. 최근 완료본 30개를 보관한다.
- 복호화 키: Mac의 `~/Library/Application Support/HomelabBackup/identity.agekey` (0600). 서버에는 공개 recipient만 있다. 복구 가능성을 위해 이 키를 별도 안전한 장소에 보관해야 한다.
- Mac 전송용 SSH 키는 `backup-channel.py`의 암호문 export와 검증 완료 ack만 실행할 수 있다. 일반 셸/포트포워딩은 차단한다.
- Mac 바이너리는 공식 age v1.3.2의 SHA-256을 검증해 전용 bin 디렉터리에 설치했다. 서버는 Ubuntu age 패키지를 사용한다.

Mac에서 즉시 복제: `/opt/homebrew/bin/python3 "$HOME/Library/Application Support/HomelabBackup/pull-backup.py"`.
결과는 같은 폴더의 `last-success.json`, `pull.log`, `pull-error.log`에 남는다. 예약 해제는 `launchctl bootout gui/$(id -u)/kr.malitda.homelab-backup`으로 한다.

`sudo /usr/local/sbin/homelab-backup`으로 즉시 실행한다. 상태는 `systemctl status homelab-backup.timer`, `journalctl -u homelab-backup.service`로 확인한다. 백업 디렉터리는 비밀정보를 포함하므로 Git에 추가하지 않는다.

복구 검증은 `sudo python3 verify-restore.py`로 실행한다. 운영 PVC를 연결하지 않고 네트워크를 차단한 임시 namespace에서 PostgreSQL/MySQL에 복원하고 namespace를 삭제한다. 임시 MySQL 초기화 서버가 아니라 PID 1의 실제 mysqld 기동 완료 후 복원을 시작한다.

전체 장애 복구에는 서버 외부에 보관한 백업이 필요하다. 동일 버전 K3s를 준비하고 중지한 상태에서 SQLite 저장소와 원래 서버 token을 복원한다. 이전 WAL/SHM 파일을 새 snapshot과 섞지 않는다. 이후 GitOps와 Sealed Secrets 키를 복구하고, **애플리케이션 DB/업로드 파일을 별도로 복원**한다. 전체 클러스터 재설치 복구 훈련은 아직 수행하지 않았다.

PV 4개는 `Retain`으로 변경했고 중요한 PVC/namespace에는 Argo CD 삭제 보호를 추가했다. Retain은 PVC 재연결 절차를 필요로 하며 백업을 대신하지 않는다.

## 감시와 알림

- `External availability`: GitHub-hosted runner, 두 공개 서비스 HTTPS 200 및 인증서 잔여 7일 검사, 실패 시 최대 3회 확인
- `Host health`: 말잇다 backend 저장소, 15분 간격으로 SSH를 통해 `/usr/local/sbin/homelab-health` 실행. `MONITOR_SSH_KEY`는 이 명령만 허용하는 전용 키이며 셸/포트포워딩은 차단한다.
- 조건: 가용 메모리 10% 미만, 디스크 여유 15% 미만, 성공 백업 27시간 초과, Mac 수신 확인 36시간 초과, 5분 이상 Ready가 아닌 Pod, Degraded/Missing/Unknown Argo 애플리케이션
- 실패는 GitHub Actions 실패로 표시된다. 실제 이메일/푸시 수신은 사용자 GitHub 알림 설정에 달려 있다. 별도 웹훅/이메일 발송 대상은 아직 지정되지 않았다.
- GitHub 스케줄은 정시 보장이 없고 공개 저장소는 장기간 활동이 없으면 예약 실행이 비활성화될 수 있다. 엄격한 가용성 감시가 필요하면 전용 외부 모니터를 추가한다.

## 관측 스택 (0단계 적용됨, 2026-09-23)

배포 알림은 argocd-notifications -> ntfy(공개 인스턴스)로 나간다. 설정은
`platform/70-argocd-notifications-cm.yaml`과 `platform/observability/secrets/ntfy.sealed.yaml`에 있다.

- 트리거는 `on-health-degraded`(urgent)와 `on-sync-failed`(high) 둘뿐이다. `on-sync-succeeded`는
  일부러 켜지 않았다 — 이미지 bump마다 알림이 오면 알림 전체를 무시하게 된다.
- 발행 URL에 토픽이 들어 있어 ConfigMap에는 `$ntfy-url` 참조만 둔다. 이 저장소는 공개다.

**git으로 표현할 수 없는 수동 단계 두 가지가 있다. 클러스터를 재구축하면 다시 해야 한다.**

1. `argocd-notifications-secret`은 argo-cd Helm 차트가 이미 만들어 둔 Secret이라, SealedSecret이
   그냥 얹지 못하고 `already exists and is not managed by SealedSecret`으로 거부된다.
   **라이브 Secret 쪽에** 애노테이션을 달아야 한다(SealedSecret 쪽에 다는 것이 아니다):

   ```sh
   kubectl annotate secret argocd-notifications-secret -n argocd \
     sealedsecrets.bitnami.com/managed="true" \
     sealedsecrets.bitnami.com/patch="true" --overwrite
   ```

   애노테이션을 나중에 달았다면 컨트롤러가 `update suppressed, no changes in spec`으로 건너뛴다.
   SealedSecret을 지웠다가 다시 apply해야 재조정된다(ownerReference가 없어 Secret은 남는다).

2. `argocd-notifications-cm`도 Helm 소유다. **`helm upgrade argocd`를 하면 차트 기본값으로 되돌아간다.**
   업그레이드 후 `kubectl apply -f platform/70-argocd-notifications-cm.yaml`을 다시 실행한다.

알림이 실제로 도착하는지는 조용히 깨지므로(4xx는 "알림이 안 오는 것"과 구별되지 않는다) 배선을 바꾼 뒤에는
반드시 실제로 발화시켜 확인한다:

```sh
POD=$(kubectl get pod -n argocd -l app.kubernetes.io/name=argocd-notifications-controller -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n argocd "$POD" -- argocd admin notifications template notify ntfy-health-degraded malitda-backend --recipient ntfy-urgent
```

응답이 HTTP 200이고 폰에 **사람이 읽을 수 있는 문장**이 떠야 통과다. 원시 JSON이 뜨면 통과가 아니다.

### 1단계 검증 기록 (2026-09-23 10:45 KST)

설계 11절의 1단계 항목을 실제로 실행한 결과다. "적용됐다"와 "동작한다"는 다르므로 수치를 남긴다.

| 검증 | 결과 |
| --- | --- |
| Prometheus active 타깃 | 13개 전부 `up`, **DOWN 0개** (k3s 내장 컴포넌트를 끈 것이 실제로 반영됐다는 증거) |
| 실행 중인 보존 설정 | `storage.tsdb.retention.time=30d`, `storage.tsdb.retention.size=14GiB` (values가 아니라 `/api/v1/status/flags` 실측) |
| PVC | prometheus 20Gi / grafana 2Gi / alertmanager 1Gi 전부 Bound |
| Grafana TLS | Let's Encrypt 발급, `notAfter=2026-12-22`, 외부 HTTPS 200 |
| Grafana 기본 비밀번호 | `admin/admin` -> **HTTP 401** (거부). SealedSecret 비밀번호 -> HTTP 200 |
| 익명 열람 | `/` -> HTTP 302 (로그인 리다이렉트) |
| 알림 전 경로 | 임시 규칙 발화 -> Alertmanager -> 릴레이 `Successfully forwarded alert to ntfy` -> ntfy 수신, **한국어 문장으로 렌더**, priority 5 |
| Alertmanager webhook 오류 | 4xx 없음 |

Grafana admin 비밀번호는 노드의 `~/.grafana-admin-password`(0600)에 있다. git에는 SealedSecret만 있다.

**남은 항목**: 폰 도착 확인은 사람이 해야 한다. ntfy 알림의 태그에 Prometheus 라벨이 그대로
붙어(`severity = critical` 등) 조금 지저분한데, `alertmanager-ntfy`에 이를 끄는 설정이 없다.

## 자원과 네트워크

말잇다와 tobehealthy에 LimitRange/ResourceQuota 및 ingress NetworkPolicy를 둔다. 같은 namespace 통신과 Ingress controller의 웹 포트, HTTP-01 solver 포트를 허용한다. 프로젝트 간 직접 접근은 차단하며 외부 API 호출을 위한 egress는 제한하지 않는다.

말잇다 liveness는 애플리케이션 생존 상태만, readiness는 생존 준비 상태와 DB를 확인한다. startupProbe가 시작 지연을 허용한다. 프론트엔드/PCM/WebSocket ready 프로토콜은 변경하지 않았다.

Argo CD/SSH/XRDP의 VPN 또는 고정 IP 제한은 접근 방식 선택 전까지 기존 상태를 유지한다.
