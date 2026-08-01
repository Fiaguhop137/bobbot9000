#!/bin/bash
cd "$(dirname "$0")"

# Kill any existing running instance
pkill -f "python3 bot.py"
sleep 1

# Open a new terminal window with the requested title
gnome-terminal --title="Emberbot137 Console" -- python3 bot.py &
