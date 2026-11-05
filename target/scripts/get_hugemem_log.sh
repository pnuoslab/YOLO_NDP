while true; do
    # 1. 수치 추출
    TOTAL=$(grep HugePages_Total /proc/meminfo | awk '{print $2}')
    FREE=$(grep HugePages_Free /proc/meminfo | awk '{print $2}')
    SIZE_KB=$(grep Hugepagesize /proc/meminfo | awk '{print $2}')
    
    # 2. 사용량 계산
    USED_COUNT=$((TOTAL - FREE))
    
    # 3. 용량 변환 (MB)
    USED_MB=$((USED_COUNT * SIZE_KB / 1024))
    
    # 4. 화면 출력과 동시에 로그 파일에 저장 (-a는 이어쓰기)
    echo "$(date +%H:%M:%S) | Used Hugepages: ${USED_MB} MB (Total: $((TOTAL * SIZE_KB / 1024)) MB)" | tee -a huge_monitor.log
    
    # 5. 1초 대기
    sleep 1
done