#!/bin/bash
export PATH=/root/jdk-17.0.12/bin:$PATH
export DISPLAY=:1

cd /root/ares/Ares
source /root/ares/Ares/venv/bin/activate
python daily_report.py 2>&1 | tee /tmp/ares_output.txt

BOT_TOKEN="8313443693:AAHmME5m12A_MK_TN7RRjim9TFi_jR3fzjI"
CHAT_ID="1231723238"

OPEN_TRADES=$(grep -A2 "OPEN POSITIONS" /tmp/ares_output.txt 2>/dev/null | tail -n +2 | head -10)
CLOSED_TODAY=$(grep "stop_loss\|take_profit\|trailing_stop\|bearish_divergence\|emotional_extreme\|mean_reversion_complete" /tmp/ares_output.txt 2>/dev/null | head -5)
REGIMES=$(grep "Market Regimes" /tmp/ares_output.txt 2>/dev/null)

if grep -q "SIGNAL:" /tmp/ares_output.txt 2>/dev/null; then
    SIGNALS=$(grep -B1 -A12 "SIGNAL:" /tmp/ares_output.txt | head -50)
    MSG="🏛️ ARES V2 - Signal Detected!
${SIGNALS}"
else
    MSG="🏛️ ARES V2 - No new signals.
${REGIMES}"
fi

if [ -n "$OPEN_TRADES" ]; then
    MSG="${MSG}
📊 Open Positions:
${OPEN_TRADES}"
fi

if [ -n "$CLOSED_TODAY" ]; then
    MSG="${MSG}
✅ Closed Today:
${CLOSED_TODAY}"
fi

MSG=$(echo "$MSG" | head -c 4000)
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d chat_id="${CHAT_ID}" \
    -d text="${MSG}"

cd /root/ares/Ares
git add logs/ -f
git diff --cached --quiet || git commit -m "Ares V2 report $(date +%Y-%m-%d_%H:%M)"
git push 2>/dev/null
