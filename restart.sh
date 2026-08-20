#!/bin/bash
sleep $((RANDOM % 10 + 1))
kill $(pgrep -f "restart.sh" | grep -v "^$$$") 2>/dev/null
cd "$(dirname "$0")"
git pull
git push
pkill -f bot.py
nohup python3 bot.py > bot_runtime.log 2>&1 &

