#!/bin/bash
export PATH=/root/jdk-17.0.12/bin:$PATH
export DISPLAY=:1

if pgrep -f ibgateway > /dev/null; then
    echo "[$(date)] IB Gateway is running"
else
    echo "[$(date)] IB Gateway is DOWN — restarting..."
    bash /root/ares/Ares/start_gateway.sh
    sleep 30
    if pgrep -f ibgateway > /dev/null; then
        echo "[$(date)] IB Gateway restarted successfully"
    else
        echo "[$(date)] IB Gateway FAILED to restart"
    fi
fi