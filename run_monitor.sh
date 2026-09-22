#!/bin/bash
export ARES_SCHEDULED=1
export PATH=/root/jdk-17.0.12/bin:$PATH
export DISPLAY=:1
cd /root/ares/Ares
source /root/ares/Ares/venv/bin/activate
python monitor_trades.py 2>&1 | tee /tmp/ares_monitor.txt

set -a
source /root/ares/.env
set +a

CLOSED=$(grep "CLOSED" /tmp/ares_monitor.txt 2>/dev/null | head -5)
POSITIONS=$(grep "📊\|❌\|✅" /tmp/ares_monitor.txt 2>/dev/null | head -10)

if [ -n "$CLOSED" ]; then
    MSG="🏛️ ARES MONITOR — Trade Closed!

${CLOSED}"
elif [ -n "$POSITIONS" ]; then
    MSG="🏛️ ARES MONITOR — Live Check

${POSITIONS}"
else
    MSG="🏛️ ARES MONITOR — No open positions"
fi

MSG=$(echo "$MSG" | head -c 4000)
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d chat_id="${CHAT_ID}" \
    -d text="${MSG}"

cd /root/ares/Ares
git add logs/
git diff --cached --quiet || git commit -q -m "Ares V3 monitor $(date +%Y-%m-%d_%H:%M)"

# See run_ares.sh for why the bare push is not sufficient.
if ! git push -q 2>/dev/null; then
    if git pull -q --rebase --autostash && git push -q 2>/dev/null; then
        echo "[$(date)] Git push succeeded after rebase onto origin"
    else
        echo "[$(date)] Git push FAILED -- logs are committed locally but not backed up"
    fi
fi
