#!/bin/bash
export PATH=/root/jdk-17.0.12/bin:$PATH
export DISPLAY=:1

cd /root/ares/Ares
source /root/ares/Ares/venv/bin/activate
python daily_report.py 2>&1 | tee /tmp/ares_output.txt

set -a
source /root/ares/.env
set +a

python3 build_dashboard.py > /tmp/ares_dashboard.txt 2>&1
MSG=$(cat /tmp/ares_dashboard.txt | head -c 4000)

curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d chat_id="${CHAT_ID}" \
    -d text="${MSG}"

cd /root/ares/Ares
git add logs/ -f
git diff --cached --quiet || git commit -m "Ares V3 report $(date +%Y-%m-%d_%H:%M)"
git push 2>&1 || echo "[$(date)] Git push FAILED"
