#!/bin/bash
export ARES_SCHEDULED=1
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
git add logs/
git diff --cached --quiet || git commit -q -m "Ares V3 report $(date +%Y-%m-%d_%H:%M)"

# Push, and if origin has moved ahead, rebase onto it and retry. A bare push
# is rejected non-fast-forward whenever a commit was made anywhere else, and
# the commit then sits on this host only -- present locally, unbacked-up, and
# the divergence never resolves itself because every later run adds another
# local commit. Data loss would need a disk failure, but a backup that has
# silently stopped working is indistinguishable from one that works until the
# day it is needed.
if ! git push -q 2>/dev/null; then
    if git pull -q --rebase --autostash && git push -q 2>/dev/null; then
        echo "[$(date)] Git push succeeded after rebase onto origin"
    else
        echo "[$(date)] Git push FAILED -- logs are committed locally but not backed up"
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" \
            -d text="WARNING: Ares git push failed. Logs committed locally on the VPS but NOT pushed to GitHub. $(git rev-list --count @{u}..HEAD 2>/dev/null || echo '?') commits unpushed." >/dev/null
    fi
fi
