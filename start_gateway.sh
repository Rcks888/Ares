#!/bin/bash
export PATH=/root/jdk-17.0.12/bin:$PATH
export DISPLAY=:1
export TWS_MAJOR_VRSN=1045

Xvfb :1 -screen 0 1024x768x24 &>/dev/null &
sleep 2

nohup /opt/ibc/gatewaystart.sh -inline --ibc_path /opt/ibc --ibc_ini /opt/ibc/config.ini -g --tws-path /root/Jts > /root/ares/gateway.log 2>&1 &
echo "IB Gateway started in background (PID: $!)"
