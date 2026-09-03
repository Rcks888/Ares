#!/bin/bash
export PATH=/root/jdk-17.0.12/bin:$PATH
export DISPLAY=:1
cd /root/ares/Ares
source /root/ares/Ares/venv/bin/activate
python monitor_trades.py 2>&1 | tee /tmp/ares_monitor.txt

BOT_TOKEN="8313443693:AAHmME5m12A_MK_TN7RRjim9TFi_jR3fzjI"
CHAT_ID="1231723238"

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
