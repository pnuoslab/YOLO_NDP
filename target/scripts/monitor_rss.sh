#!/bin/bash
# ============================================================
# 스토리지 노드 RSS 모니터링 스크립트
# 스토리지 노드에서 실행
# 사용법: bash monitor_rss.sh [출력파일명]
# 예시:   bash monitor_rss.sh ~/rss_15s.log
# ============================================================

OUTPUT="${1:-/tmp/rss_monitor.log}"

SPDK_PID=$(pgrep -f nvmf_tgt | tail -1)
if [ -z "$SPDK_PID" ]; then
    echo "ERROR: nvmf_tgt 프로세스를 찾을 수 없음"
    exit 1
fi

echo "SPDK PID: $SPDK_PID"
echo "출력 파일: $OUTPUT"
echo "모니터링 시작: $(date)"
echo "Ctrl+C로 종료 후 결과 확인"
echo ""

echo "# timestamp RSS_kB" > $OUTPUT

while true; do
    RSS=$(grep VmRSS /proc/$SPDK_PID/status | awk '{print $2}')
    TS=$(date +%H:%M:%S)
    echo "$TS $RSS" >> $OUTPUT  # 파일만, 터미널 echo 제거
    sleep 0.3
done
