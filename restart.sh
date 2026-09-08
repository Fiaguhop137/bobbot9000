#!/bin/bash

export PATH=/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin
export HOME=/home/firebot
export USER=firebot
export LOGNAME=firebot
export XDG_RUNTIME_DIR=/run/user/1000

sleep $((RANDOM % 10 + 1))

kill $(/usr/bin/pgrep -f "restart.sh" | /usr/bin/grep -v "^$$$") 2>/dev/null || :

cd /home/firebot/git/Emberbot137

/usr/bin/git pull
/usr/bin/git push

/usr/bin/pkill -f "bot.py" || :

/usr/bin/nohup /usr/bin/python3 bot.py > bot_runtime.log 2>&1 &
