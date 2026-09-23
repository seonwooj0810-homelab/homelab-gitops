#!/bin/sh
# 최근 성공 백업 시각을 node-exporter textfile collector가 읽을 .prom 파일로 기록한다.
#
# 왜 필요한가: 백업은 호스트의 systemd 타이머가 /var/backups/homelab/ 에 쓰는 것이라
# 클러스터 안에서는 보이지 않는다. Prometheus가 백업 신선도를 알 수 있는 유일한 경로다.
# 이 값을 BackupStale 규칙(platform/observability/rules/homelab-rules.yaml)이 쓴다.
#
# 설치(수동, 2단계):
#   sudo install -D -m 0755 operations/backup-textfile-exporter.sh \
#     /usr/local/sbin/homelab-backup-textfile
#   sudo install -d -m 0755 /var/lib/node_exporter/textfile
#   # homelab-backup.service 에 아래 한 줄을 추가하고 daemon-reload
#   #   ExecStopPost=/usr/local/sbin/homelab-backup-textfile
#
# 원자적으로 쓴다 — node-exporter가 반쯤 쓰인 파일을 읽으면 스크랩이 통째로 실패한다.
set -eu

TEXTFILE_DIR=${TEXTFILE_DIR:-/var/lib/node_exporter/textfile}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/homelab}
OUT="$TEXTFILE_DIR/homelab_backup.prom"
TMP="$OUT.$$"

[ -d "$TEXTFILE_DIR" ] || exit 0

# latest 심볼릭 링크가 가리키는 완료본의 mtime을 성공 시각으로 본다.
if [ -L "$BACKUP_ROOT/latest" ] && [ -d "$BACKUP_ROOT/latest" ]; then
	TS=$(stat -c %Y "$BACKUP_ROOT/latest/" 2>/dev/null || echo 0)
	SUCCESS=1
else
	TS=0
	SUCCESS=0
fi

{
	echo "# HELP homelab_backup_last_success_timestamp_seconds 마지막으로 성공한 백업의 UNIX 시각."
	echo "# TYPE homelab_backup_last_success_timestamp_seconds gauge"
	echo "homelab_backup_last_success_timestamp_seconds $TS"
	echo "# HELP homelab_backup_last_run_success 마지막 백업 실행이 완료본을 남겼는지."
	echo "# TYPE homelab_backup_last_run_success gauge"
	echo "homelab_backup_last_run_success $SUCCESS"
} > "$TMP"

chmod 0644 "$TMP"
mv -f "$TMP" "$OUT"
