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
2. 이 공개 운영 저장소의 `Update Malitda images`가 이미지의 `production` digest를 조회한다. 비공개인 `malitda-web`은 저장소 secret `GHCR_READ_TOKEN`(read:packages PAT)으로 조회한다.
3. digest 변경을 `apps/malitda/{backend,frontend}/kustomization.yaml`에 커밋한다.
4. Argo CD `malitda-backend`, `malitda-frontend`가 Git 변경을 적용한다.

소스 저장소에 대한 Argo CD 자격증명은 필요 없다. 백엔드 이미지는 공개, 웹 이미지(`ghcr.io/malitda/malitda-web`)는 비공개라 클러스터는 `apps/malitda/frontend/secrets/ghcr-pull.sealed.json`으로 pull한다. 이 SealedSecret과 `GHCR_READ_TOKEN`은 같은 PAT이므로 만료·회전 시 둘 다 갱신한다. 평문 Secret은 저장하지 않고 SealedSecret만 커밋한다.

이미지 점검 cron은 5분 간격이지만 GitHub 예약 실행은 지연될 수 있다. 긴급 배포는 이미지 발행 완료 후 `gh workflow run malitda-images.yml -R seonwooj0810-homelab/homelab-gitops`로 실행한다. CI 성공은 이미지 발행 성공이며 서비스 반영 완료는 Argo CD의 `Synced / Healthy`로 판단한다.

Kubernetes 설정은 **이 저장소의 apps/malitda**에서만 변경한다. 앱 저장소에 있던 `deploy/` 참고본은 2026-09-22에 삭제했다 — 내용이 이 저장소와 동일한 중복이라, 그쪽을 고치고 반영됐다고 오해할 여지가 실제 이득보다 컸다.

네임스페이스·시크릿이 갖춰졌는지 확인하고 현재 상태를 훑어보려면 노드에서 `operations/malitda-bootstrap.sh`를 실행한다(말잇다 backend 저장소의 `deploy/deploy.sh`가 여기로 옮겨온 것이다). 배포는 하지 않는다 — 적용 책임은 Argo CD 한 곳에 있다.

롤백할 때는 먼저 `Update Malitda images` 워크플로를 일시 중지하고, 이전 digest 커밋으로 되돌린 뒤 Argo CD를 확인한다. `production` 태그도 원하는 버전으로 재발행한 뒤 워크플로를 재개한다. 워크플로를 켜 둔 채 Git만 되돌리면 다음 점검에서 다시 최신 production digest로 바뀐다. DB 스키마는 이미지 롤백으로 되돌아가지 않으므로 호환성을 별도 확인한다.

## 풀필먼트(fulfillment) GitOps

`fulfillment.junghaebom.com` 하나에 웹과 API를 둔다. Ingress가 `/api`는 `backend`(8080), `/`는 `frontend`(3000)로 보낸다.
같은 오리진이라 CORS 설정이 없다. `*.junghaebom.com` 와일드카드 DNS가 이미 이 서버를 가리키므로 DNS 작업은 없다.

- 이미지는 앱 레포 CI가 직접 반영한다(`gha-templates/*-deploy.yml` 방식, 말잇다의 5분 폴링을 쓰지 않는다).
  `fulfillment-junghaebom/fulfillment-{backend,web}`의 `main` push → `ghcr.io/fulfillment-junghaebom/fulfillment-{backend,web}:sha-<12자리>`
  발행 → `apps/fulfillment/{backend,frontend}/kustomization.yaml`의 `newTag`를 커밋 → Argo CD가 적용한다.
  두 앱 레포에 `MANIFEST_REPO_TOKEN`(이 저장소 contents 쓰기 권한) 시크릿이 있어야 한다.
- 백엔드 이미지 빌드는 테스트를 돌리지 않는다. 테스트가 레포 밖 `../data/vendors`(수령인 개인정보)를 읽어서, 그 데이터를 CI에 두지 않기 위해서다.
- 로그인 세션이 백엔드 메모리에 있어 `replicas: 1`이다. 백엔드가 재시작되면 다시 로그인해야 한다.
- 백엔드에 actuator가 없어 probe는 tcpSocket(8080)이다. 웹 probe는 `/login`이다(`/`는 리다이렉트).

처음 한 번만 하는 일:

```sh
# 1) Argo CD Application 등록 — bootstrap은 ApplicationSet 대상이 아니라 직접 apply한다
kubectl apply -f bootstrap/fulfillment-backend.yaml -f bootstrap/fulfillment-frontend.yaml
# 2) 첫 이미지 — 두 앱 레포 main에 push해 CI를 한 번씩 돌린다. 그 전까지 newTag가 bootstrap이라 ImagePullBackOff다
# 3) 관리자 비밀번호 확인 — 봉인본을 만들 때 노드에서 무작위로 만들어 평문이 남아 있지 않다
kubectl -n fulfillment get secret fulfillment-backend -o jsonpath='{.data.ADMIN_PASSWORD}' | base64 -d; echo
# 4) 백업 — fulfillment postgres가 뜬 뒤에 설치본을 교체한다. 먼저 교체하면 pg_dump 실패로 전체 백업이 BACKUP_FAILED가 된다
sudo install -m 0755 operations/backup.py /usr/local/sbin/homelab-backup
```

시크릿은 노드에서 봉인했다(값이 터미널 밖으로 나가지 않게). **postgres 비밀번호는 PVC 초기화 때 한 번만 쓰인다** — 봉인본을
다시 만들면 DB 비밀번호와 어긋나 백엔드가 접속하지 못한다. 관리자 계정도 `admin_account`가 비어 있을 때만 만들어지므로,
봉인본을 바꿔도 이미 만들어진 계정의 비밀번호는 바뀌지 않는다.

```sh
cd ~/workspace/k8s-manifests && D=apps/fulfillment/backend/secrets
SEAL='kubeseal --controller-name sealed-secrets --controller-namespace sealed-secrets-system --format json'
# postgres — POSTGRES_USER는 05-postgres.yaml의 pg_isready -U 값, POSTGRES_DB는 backend-config의 DB_URL과 같아야 한다
kubectl create secret generic fulfillment-postgres -n fulfillment --dry-run=client -o yaml \
  --from-literal=POSTGRES_DB=fulfillment --from-literal=POSTGRES_USER=fulfillment \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)" | $SEAL > $D/fulfillment-postgres.sealed.json
# backend — 첫 관리자 계정
kubectl create secret generic fulfillment-backend -n fulfillment --dry-run=client -o yaml \
  --from-literal=ADMIN_USERNAME=admin \
  --from-literal=ADMIN_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=')" | $SEAL > $D/fulfillment-backend.sealed.json
# ghcr-pull — 말잇다 네임스페이스의 같은 PAT를 fulfillment 네임스페이스용으로 다시 봉인한다(웹과 백엔드가 같이 쓴다)
kubectl get secret ghcr-pull -n malitda -o json | python3 -c 'import json,sys;s=json.load(sys.stdin);print(json.dumps({"apiVersion":"v1","kind":"Secret","type":s["type"],"metadata":{"name":"ghcr-pull","namespace":"fulfillment"},"data":s["data"]}))' \
  | $SEAL > $D/ghcr-pull.sealed.json
```

PAT를 회전하면 말잇다·fulfillment `ghcr-pull` 봉인본을 함께 갱신한다.

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

**폰 도착 확인 완료 (2026-09-23 13:20 KST, 사용자 확인).** 이것으로 설계 11절 1단계 검증
4개 항목이 모두 닫혔다. 서버 쪽 발행 기록은 12시간 내 9건이었고, 그중 2건(10:35·10:40
`obs-kube-prometheus-stack Degraded`)은 테스트가 아니라 **배포 중 실제로 발화한 것**이다 —
0단계 구독이 실전에서 동작한다는 증거다.

**알림 링크(`X-Click`)는 한 번 잘못 넣었다가 고쳤다.** Prometheus의 `.GeneratorURL`을 그대로
썼는데 그 값은 `http://kube-prometheus-stack-prometheus.observability:9090/...` 즉 클러스터 내부
주소라 폰에서 이름 해석이 안 된다. Prometheus는 설계 9절에 따라 Ingress가 없으므로 외부에서
닿는 것은 Grafana뿐이다. `https://grafana.geonganghaejim.site/alerting/list`로 바꿨다.

**남은 흠**: ntfy 알림의 태그에 Prometheus 라벨이 그대로 붙어(`severity = critical` 등)
조금 지저분한데, `alertmanager-ntfy`에 이를 끄는 설정이 없다.

### 2단계 검증 기록 (2026-09-23 11:25 KST)

| 검증 | 결과 |
| --- | --- |
| **삭제된 Pod의 로그 조회** | **통과.** `kubectl logs`가 NotFound인 상태에서 Loki가 같은 줄을 돌려줬다 |
| 유입 | 최근 3분 기준 argocd 340 / observability 119 / kube-system 6 줄 |
| Alloy `dropping data` | 0건 |
| Loki 수신 오류 | 500 · `no schema` · `too far behind` 모두 없음 |
| journald 사용량 | 2.3G -> **515M** (상한 1G) |
| 백업 신선도 메트릭 | Prometheus에서 `homelab_backup_last_success_timestamp_seconds` 조회됨 (빈 결과 아님) |

**알려진 한계**: 새로 만들어진 Pod의 **처음 약 6초(대략 3줄)는 Loki에 남지 않는다.**
Alloy가 `tail_from_end`로 동작하기 때문이며, 이유와 대안은 설계 문서 Ruling R9에 있다.
오래 돌다 죽는 Pod는 Alloy가 내내 tail하고 있어 이 한계와 무관하다. 기동 직후 죽는
CrashLoopBackOff의 원인 줄을 놓치는 것이 문제가 되면 R9의 대안으로 바꾼다.

**호스트 쪽 수동 설치분** (클러스터 재구축 시 다시 해야 한다):

```sh
sudo install -D -m 0644 operations/journald-homelab.conf /etc/systemd/journald.conf.d/homelab.conf
sudo systemctl restart systemd-journald
sudo install -d -m 0755 /var/lib/node_exporter/textfile
sudo install -D -m 0755 operations/backup-textfile-exporter.sh /usr/local/sbin/homelab-backup-textfile
sudo install -D -m 0644 operations/homelab-backup-textfile.conf \
  /etc/systemd/system/homelab-backup.service.d/textfile.conf
sudo systemctl daemon-reload
```

Alloy의 positions는 노드의 `/var/lib/alloy`에 있다. 지우면 그 시점 이후부터 다시 수집한다.

### 3단계: DB exporter용 MySQL 계정 (수동 DDL)

`mysql-exporter`는 전용 `exporter` 계정으로 붙는다. **이 계정 생성은 GitOps로 표현되지 않는다.**
DB를 새로 만들면 다시 실행해야 한다. 비밀번호는
`apps/tobehealthy/overlays/prod/secrets/mysql-exporter-credentials.sealed.yaml`에 봉인돼 있고,
노드의 `~/.mysql-exporter-password`(0600)에도 있다.

```sql
CREATE USER IF NOT EXISTS 'exporter'@'%' IDENTIFIED BY '<비밀번호>' WITH MAX_USER_CONNECTIONS 3;
ALTER USER 'exporter'@'%' IDENTIFIED BY '<비밀번호>';
GRANT SELECT, PROCESS, REPLICATION CLIENT ON *.* TO 'exporter'@'%';
FLUSH PRIVILEGES;
```

**root로는 안 된다.** 이 MySQL에는 `root@localhost`만 있고 `root@%`가 없어서 Pod에서 붙으면
`Access denied for user 'root'@'10.42.x.x'`가 난다. 앱 사용자 `app@%`는 PROCESS와
REPLICATION CLIENT가 없어 절반만 수집된다.

비밀번호를 바꾸면 위 `ALTER USER`와 SealedSecret 재봉인을 함께 해야 한다.

### 3단계 검증 기록 — DB 메트릭 (2026-09-23 11:35 KST)

| 검증 | 결과 |
| --- | --- |
| Prometheus active 타깃 | 16개 전부 `up`, **DOWN 0개** (exporter 3종 추가 후에도 유지) |
| `mysql_up` | 1 — `threads_connected=11`, `uptime=385639`, `queries=54305` |
| `redis_up` | 1 — `connected_clients=3` |
| `pg_up` | 1 — `pg_stat_database_numbackends`에 실제 값 |

수치가 붙은 것은 "비어 있지 않은 결과"를 확인했다는 뜻이다. NetworkPolicy가 조용히 막으면
정확히 빈 결과로 보이므로(설계 11절) 값까지 본다.

### 3단계 검증 기록 — 앱 메트릭 (2026-09-23 11:45 KST)

| 대상 | 상태 |
| --- | --- |
| **tobehealthy backend** | **완료.** 앱 레포 변경 없이 이 저장소만 고쳐서 붙었다 |
| **malitda backend** | **완료.** [malitda/malitda-backend#1](https://github.com/malitda/malitda-backend/pull/1) 머지 -> 이미지 배포 -> ServiceMonitor 추가 |

tobehealthy backend 실측 (`job="backend"`):

| 메트릭 | 시계열 | 값 |
| --- | --- | --- |
| `jvm_memory_used_bytes` | 8 | 16343176, 1270440, … |
| `hikaricp_connections_active` | 1 | 0 |
| `http_server_requests_seconds_count` | 4 | 2, 9, … |
| `process_uptime_seconds` | 1 | 47678 |

Prometheus active 타깃 17개 전부 `up`, DOWN 0개.

**이 과정에서 잡힌 것 두 가지** (둘 다 "조용히 아무것도 안 되는" 모양이었다):

1. 이 백엔드의 actuator는 **8080이 아니라 7070**이다(`management.server.port`).
   NetworkPolicy에 8080을 열어 두고 `observability`에서 붙지 못했다.
   같은 네임스페이스에서는 200이 나와서, 네임스페이스를 바꿔 재현하기 전까지는 정상으로 보였다.
2. `backend` Service에 **라벨이 없었다.** ServiceMonitor의 `selector.matchLabels`가
   아무것도 매치하지 않는데, 이 상태는 `DOWN`이 아니라 **"타깃이 목록에 아예 없음"**으로 나타나서
   `DOWN == 0` 검사를 그대로 통과한다. `app: backend` 라벨을 Service에 추가했다.

**malitda 마무리 (2026-09-23 13:35 KST).** PR #1 머지 -> CI `verify`·`deploy` 성공 ->
`malitda-images` 워크플로가 digest를 커밋 -> Argo 동기화 -> 롤아웃 확인 순으로 진행했고,
**`/actuator/prometheus`가 200을 돌려주는 것을 먼저 확인한 뒤에** ServiceMonitor를 추가했다.
순서를 뒤집으면 404로 영구 DOWN 타깃이 생기고, 그것이 설계 12절이 지목한
"상시 오탐 -> 알림 전체를 무시" 경로다.

malitda도 **같은 함정 두 개**를 그대로 갖고 있어 함께 고쳤다 — Service에 라벨이 없었고,
포트에 이름조차 없었다(`- port: 8080`). `app: backend` 라벨과 `name: http`를 붙였다.

malitda backend 실측 (`namespace="malitda"`):

| 메트릭 | 시계열 | 값 |
| --- | --- | --- |
| `jvm_memory_used_bytes` | 8 | 14255512, 1228184, … |
| `http_server_requests_seconds_count` | 2 | 41, 3 |
| `jvm_threads_live_threads` | 1 | 24 |
| `process_uptime_seconds` | 1 | 192 |

**Prometheus active 타깃 18개 전부 `up`, DOWN 0개.** 이것으로 설계의 0~3단계가 모두 닫혔다.

### 알림 규칙 점검 (2026-09-23 11:55 KST)

규칙이 "존재하는 것"과 "평가되는 것"과 "발화하는 것"은 각각 다르다. 셋 다 확인했다.

| 확인 | 결과 |
| --- | --- |
| 로드된 규칙 | 31개 그룹 / 221개 규칙, **`health != ok` 인 규칙 0개** |
| `BackupStale` | `state=inactive health=ok severity=critical` — 존재하고 평가 중 |
| `BackupMetricMissing` | `state=inactive health=ok severity=warning` |
| `KubePersistentVolumeFillingUp` | 두 변형 **모두 `severity=critical`** — values의 customRules가 실제로 먹었다 |
| `Watchdog` | `state=firing` — 의도대로 null 리시버로 버려져 폰에는 오지 않는다 |

`BackupStale`이 실제로 참이 되는지는 textfile 메트릭을 **일시적으로 30시간 전으로 조작해** 확인했다.
식이 1건(108047초)을 반환했고, `absent()`는 0건이었다. 확인 후 백업본으로 원복했고
(문자열 역치환이 아니라 파일 복원) 식이 다시 0건인 것까지 확인했다.

**아직 한 번도 실행되지 않은 것**: `homelab-backup.service`의 `ExecStopPost` drop-in.
스크립트는 수동으로만 돌렸다. 다음 04:00 타이머 실행이 첫 실전이므로
`journalctl -u homelab-backup.service` 로 확인할 것.

### Grafana 대시보드 (2026-09-23 13:45 KST)

**홈랩 한눈에** — https://grafana.geonganghaejim.site/d/homelab-overview/

정본은 `platform/observability/dashboards/homelab-overview.configmap.yaml`이다.
Grafana 사이드카가 `grafana_dashboard: "1"` 라벨을 보고 자동으로 올린다.

**UI에서 손으로 만들지 않는다.** Grafana PVC(2Gi, local-path)는 설계상 소모성이라
볼륨이 날아가면 UI에서 만든 대시보드는 함께 사라진다. ConfigMap으로 두면 Argo가 다시 만든다.
UI에서 편집해 저장해도 되지만 **다음 sync 때 파일 내용으로 되돌아간다** — 고칠 때는
JSON Model을 복사해 파일에 반영한다.

구성(위에서 아래로 노드 → 용량 → 앱/DB → 로그):

| 구역 | 패널 |
| --- | --- |
| stat 6 | 노드 CPU · 메모리 · 루트 디스크 여유 · Pod 재시작(1h) · 백업 경과 · 스왑 |
| 추세 | 노드 CPU·메모리 / 루트 디스크 여유 + PVC 사용률 |
| 앱·DB | 서비스별 JVM 힙 / HTTP 요청률(결과별) / DB 연결 수 |
| 로그 | Loki `error\|exception\|fatal\|panic` 전 네임스페이스 |

검증: 배치 전에 패널 쿼리를 전부 직접 던져 **비어 있지 않은 결과**를 확인했고,
배치 후 Grafana API(`/api/ds/query`)로 Prometheus·Loki 양쪽이 값을 돌려주는 것까지 봤다
(`provisioned=true`, 패널 12개). 유일하게 비어 있는 것은 `SERVER_ERROR` 계열인데
아직 5xx가 난 적이 없어서다 — `outcome` 라벨 자체는 `CLIENT_ERROR`/`SUCCESS`로 존재한다.

**주의할 점 하나**: `kubelet_volume_stats_*`는 `job="kubelet"`과 `job="apiserver"` 두 벌로
잡힌다. 필터하지 않으면 같은 PVC가 두 선으로 겹쳐 보인다. 패널 쿼리에 필터를 넣어 뒀다.

### 로그 노출 점검과 레벨 조정 (2026-09-23 14:05 KST)

Grafana가 공개돼 있고 Loki에 전 네임스페이스 로그가 들어오므로, **로그인한 사람은
Explore에서 모든 Pod 로그를 읽을 수 있다.** 대시보드에 무엇을 그리느냐와 무관한 사실이다.

**민감정보 스캔 (최근 6시간 37,543줄 대상, 값은 보지 않고 건수만)**

| 패턴 | 건수 | 판정 |
| --- | --- | --- |
| 이메일 · JWT · Bearer · 카드번호 | 0 | — |
| 주민번호 형태 | 54 | **오탐** — Loki compactor 파일명의 UNIX ms 타임스탬프 |
| 전화번호 형태 | 10 | **오탐** — argocd repo-server의 `time_ms=4.333018` 류 소수 |
| `password=` | 5 | **오탐** — MySQL 표준 에러 `(using password: YES)` |
| `secret=` | 6 | **오탐** — SealedSecret **리소스 이름**(값 아님) |

전부 인프라 컴포넌트에서 나왔고 **애플리케이션(tobehealthy·malitda) 로그에는 한 건도 없었다.**

**조치**: `malitda/backend`의 `logging.level.kr.malitda`가 `DEBUG`였다.
지금 깨끗한 것은 코드가 그렇게 로깅하지 않아서일 뿐 구조적 보장이 아니므로 `INFO`로 내렸다
([malitda/malitda-backend#2](https://github.com/malitda/malitda-backend/pull/2), 머지·배포 완료).

`tobehealthy/backend`는 손대지 않았다 — `logback-spring.xml`의 root가 이미 `INFO`이고,
SQL을 파라미터까지 찍는 p6spy는 `dev` 프로파일에서만 켜진다(운영은 base라 꺼짐).

**배포 후 확인**: Pod 로그는 INFO 28 / WARN 4, DEBUG 0. Loki 기준 최근 10분
malitda DEBUG **0줄**, 같은 구간 전체 **58줄** — 파이프라인이 살아 있는 상태에서의 0이다.

**남는 것**: 조정 이전의 DEBUG 로그는 Loki 보존 기간(14일)만큼 남아 있다.
위 스캔에서 민감정보가 없음을 확인했으므로 별도 삭제는 하지 않았다.

**아직 하지 않은 것 (사용자가 보류)**: 읽기 전용 Viewer 계정 추가, Ingress 출처 IP 제한.
현재 Grafana 계정은 `admin` 하나뿐이고 Grafana 전체 관리자 권한이다.

### 로그 패널 되먹임과 Loki 로그량 (2026-09-23 14:55 KST)

대시보드 로그 패널이 Loki 자신의 로그로 가득 찬 것을 사용자가 발견했다. **k3s 로그가 아니었다.**

**원인은 고리다.** Loki는 쿼리 하나마다 `caller=metrics.go`로 쿼리 문자열과 통계 수십 개를
INFO로 찍는다. 이 패널의 쿼리에 `error|exception|fatal|panic`이 들어 있으므로 그 기록에도
그 단어들이 들어간다. Alloy는 Loki 로그도 수집하므로, 패널이 **자기 쿼리를 자기가 잡는다.**
Grafana 자동 새로고침(1분)이 이 고리를 계속 돌린다.

**조치 (양쪽 모두)**

1. Loki `server.log_level: warn` — `metrics.go`·`engine.go`·`table_manager.go` 계열 INFO가 사라진다
2. 패널 쿼리에 `!= caller=metrics.go` 등 명시적 배제 — 1번이 나중에 바뀌어도 패널은 깨끗하다

**실측 (시간당 환산)**

| 항목 | 조치 전 | 조치 후 | 감소 |
| --- | --- | --- | --- |
| Loki 컨테이너 로그 | 18,050 | 708 | **96%** |
| 클러스터 전체 로그 | 26,425 | 7,920 | **70%** |
| 로그 패널이 잡는 줄 | 7,884 | 180 | **98%** |

조치 전에는 패널이 잡은 7,884줄 중 **7,760줄(98.4%)이 Loki 자기 로그**였고,
Loki 혼자 전체 로그량의 **68%**를 차지했다.

**2차 조치 (14:20 KST)** — 위 조치 후 남은 잡음 두 건을 근원에서 없앴다.

1. **argocd-notifications-controller `info` → `warn`.** Application마다 트리거 평가 결과를
   INFO로 찍어 Loki를 잡은 뒤 로그량 1위(시간당 약 3,240줄)가 됐다. 거기 나오는
   `Trigger ... FAILED`는 오류가 아니라 **조건이 맞지 않았다**는 뜻이다(Argo의 혼란스러운 문구).
   `argocd-cmd-params-cm`의 `notificationscontroller.log.level`로 바꿨다 —
   `platform/71-argocd-cmd-params-cm.yaml`, `platform-core`가 self-heal 한다.
   **이 ConfigMap은 Helm 소유이고 `server.insecure: "true"` 같은 핵심 키를 담고 있어**
   라이브 21개 키를 그대로 옮기고 한 키만 바꿨다(대조해 차이 1건 확인). 적용 후
   키 개수 21 유지와 Argo CD UI HTTP 200을 확인했다.

2. **Loki 링 `memberlist` → `inmemory`.** replica 1 + filesystem 배포에 가십 링이 필요 없는데,
   기동할 때마다 자기 헤드리스 서비스를 찾다 NXDOMAIN으로 warn/error를 뱉었다
   (`failed to fast-join the memberlist cluster`). 몇 초 뒤 해소되지만 재시작마다 패널에 뜬다.
   `loki.commonConfig.ring.kvstore.store: inmemory`로 경로 자체를 없앴다.
   **replica를 늘리려면 되돌려야 한다** — 다만 그건 오브젝트 스토리지가 먼저 필요해
   차트 validate가 막는다(설계 5절).

**최종 실측 (시간당)**

| 항목 | 최초 | 1차 후 | 2차 후 | 총 감소 |
| --- | --- | --- | --- | --- |
| Loki 컨테이너 | 18,050 | 708 | **0** | 100% |
| 클러스터 전체 | 26,425 | 7,920 | **3,792** | **86%** |
| 로그 패널 매치 | 7,884 | 180 | **108** | **99%** |

2차 후에는 패널의 구버전 쿼리(배제 필터 없음)와 신버전 쿼리가 **둘 다 108줄로 같다** —
되먹임이 근원에서 사라졌다는 뜻이다. 배제 필터는 안전망으로 남겨 둔다.

로그량 상위는 이제 argocd repo-server(1,440) / application-controller(1,368)다. 정상 수준이다.

전환 직후 Loki에 `ratestore.go: error getting ingester clients err="empty ring"`이 한 번
찍혔다. 링을 바꾸는 순간의 일회성이며 이후 재발하지 않는다.

### 로그 파이프라인 자기 감시 (2026-09-23 15:20 KST)

**문제**: Loki와 Alloy가 스크랩되지 않아 **로그 수집이 멈춰도 아무도 모르는** 상태였다.
가정이 아니다 — 이 스택을 만드는 동안 네 번 그 상태가 됐다:

| 실패 | 겉보기 |
| --- | --- |
| Alloy `__path__` 조합이 아무 파일도 매치 안 함 | Pod Running, 에러 없음, Loki에 0건 |
| 오래된 항목 때문에 배치가 통째로 400 거부 | `start tailing file` 만 보임 |
| `stage.drop` 이 전량 폐기 | 동일 |
| schema 범위 밖 항목으로 push가 500 | 동일 |

네 번 모두 사람이 직접 쿼리를 던지기 전에는 정상과 구별되지 않았다.

**조치**

1. `monitoring.serviceMonitor`(Loki) · `serviceMonitor`(Alloy) 활성화 → 타깃 20개 전부 `up`
2. `homelab.logpipeline` 규칙 그룹 추가

| 규칙 | 조건 | 심각도 | 잡는 것 |
| --- | --- | --- | --- |
| `LogIngestionStopped` | 15분간 Loki 수신 0줄 | critical | 파이프라인은 살아 있는데 데이터가 안 흐름 |
| `LogEntriesDropped` | 10분간 Alloy가 1건이라도 폐기 | warning | 일부만 버려져 위 규칙에 안 걸리는 경우 |

스크랩 대상이 죽는 경우는 차트 기본 `TargetDown`이 이미 잡으므로 쓰지 않았다(설계 8.3).

**검증**: 규칙 223개 중 `health != ok` **0개**, 두 규칙 모두 `inactive`(정상).
식 자체는 이렇게 확인했다 —

```
기반값(15분 수신)              544줄      <- 흐르고 있다
발화식 `... == 0`              0건        <- 발화 안 함 (정상)
비교연산 대조군 `폐기 == 0`      1건 (0.0)  <- == 0 비교가 동작함을 증명
```

마지막 줄이 중요하다. 발화식이 빈 것이 **"식이 깨져서"가 아니라 "조건이 참이 아니라서"**임을
보여준다. 이 대조군이 없으면 빈 결과는 통과의 증거가 되지 못한다(설계 11절).

## 자원과 네트워크

말잇다와 tobehealthy에 LimitRange/ResourceQuota 및 ingress NetworkPolicy를 둔다. 같은 namespace 통신과 Ingress controller의 웹 포트, HTTP-01 solver 포트를 허용한다. 프로젝트 간 직접 접근은 차단하며 외부 API 호출을 위한 egress는 제한하지 않는다.

말잇다 liveness는 애플리케이션 생존 상태만, readiness는 생존 준비 상태와 DB를 확인한다. startupProbe가 시작 지연을 허용한다. 프론트엔드/PCM/WebSocket ready 프로토콜은 변경하지 않았다.

Argo CD/SSH/XRDP의 VPN 또는 고정 IP 제한은 접근 방식 선택 전까지 기존 상태를 유지한다.
