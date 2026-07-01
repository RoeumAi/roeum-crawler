#!/bin/bash
CRAWLER_DIR="/Users/loum/loum/roeum-crawler"
PYTHON="/Users/loum/miniconda3/envs/crawler/bin/python"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DATE_STR=$(date "+%Y-%m-%d %H:%M")
LOG_FILE="$CRAWLER_DIR/logs/weekly_${TIMESTAMP}.log"
DISCORD_WEBHOOK=$(grep '^DISCORD_WEBHOOK=' "$CRAWLER_DIR/.env" | cut -d'=' -f2-)

mkdir -p "$CRAWLER_DIR/logs"
cd "$CRAWLER_DIR"

START_TIME=$(date +%s)

echo "======================================" >> "$LOG_FILE"
echo "주간 업데이트 시작: $(date)" >> "$LOG_FILE"
echo "======================================" >> "$LOG_FILE"

"$PYTHON" -u crawl.py --mode update --since 7 --concurrent 3 >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

END_TIME=$(date +%s)
DURATION=$(( (END_TIME - START_TIME) / 60 ))

echo "종료: $(date)" >> "$LOG_FILE"

# Discord 알림 전송
"$PYTHON" - << PYEOF
import re, json, urllib.request

log_file = "$LOG_FILE"
webhook_url = "$DISCORD_WEBHOOK"
duration = $DURATION
exit_code = $EXIT_CODE
date_str = "$DATE_STR"

with open(log_file, 'r', encoding='utf-8') as f:
    content = f.read()

scrapers = ['law', 'adrule', 'case', 'decision', 'interpretation', 'mediation_case', 'judgment']
names = {
    'law': '법령', 'adrule': '행정규칙', 'case': '판례',
    'decision': '심의결정례', 'interpretation': '해석례',
    'mediation_case': '조정사건례', 'judgment': '주요판정사례'
}

lines = content.split('\n')
results = {}
current = None

for line in lines:
    for scraper in scrapers:
        if f'({scraper})' in line and ('✅' in line or '❌' in line):
            current = scraper
            results[scraper] = {'status': '✅' if '✅' in line else '❌', 'success': 0, 'skipped': 0, 'failed': 0, 'total': 0}
            break
    if current:
        def pnum(pattern, text):
            m = re.search(pattern + r'[^0-9]*([\d,]+)', text)
            return int(m.group(1).replace(',', '')) if m else None
        v = pnum(r'발견 URL', line)
        if v is not None: results[current]['total'] = v
        v = pnum(r'성공', line)
        if v is not None: results[current]['success'] = v
        v = pnum(r'실패', line)
        if v is not None: results[current]['failed'] = v
        v = pnum(r'건너뜀', line)
        if v is not None: results[current]['skipped'] = v

total_updated = sum(r.get('success', 0) for r in results.values())
hours = duration // 60
mins = duration % 60
duration_str = f"{hours}시간 {mins}분" if hours > 0 else f"{mins}분"

out = [f"📊 **주간 크롤러 업데이트** ({date_str})\n"]

for scraper in scrapers:
    r = results.get(scraper)
    name = names[scraper]
    if not r:
        out.append(f"⬜ {name}: 미실행")
    elif r['status'] == '❌':
        out.append(f"❌ {name}: 오류 발생")
    else:
        updated = r['success']
        skipped = r['skipped']
        if updated > 0:
            out.append(f"✅ {name}: **{updated}건 업데이트** (건너뜀 {skipped}건)")
        else:
            out.append(f"✅ {name}: 변경 없음 (건너뜀 {skipped}건)")

out.append(f"\n🔢 전체 업데이트: **{total_updated}건**  ⏱️ 소요시간: {duration_str}")

msg = '\n'.join(out)
payload = json.dumps({"content": msg}).encode('utf-8')
req = urllib.request.Request(webhook_url, data=payload, headers={'Content-Type': 'application/json', 'User-Agent': 'DiscordBot (private, 1.0)'}, method='POST')
try:
    urllib.request.urlopen(req)
    print("Discord 알림 전송 완료")
except Exception as e:
    print(f"Discord 알림 전송 실패: {e}")
PYEOF
