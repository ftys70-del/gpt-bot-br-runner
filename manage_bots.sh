#!/bin/bash
# Usage:
#   ./manage_bots.sh start_all       - start all bots
#   ./manage_bots.sh stop_all        - stop all bots
#   ./manage_bots.sh restart <login> - restart a single bot
#   ./manage_bots.sh stop <login>    - stop a single bot
#   ./manage_bots.sh start <login>   - start a single bot
#   ./manage_bots.sh status          - show status of all bots

DIR="/root/grepolisbots"
PYTHON="$DIR/venv/bin/python3"
SCRIPT="$DIR/bot_single.py"
LOGDIR="$DIR/logs"
mkdir -p "$LOGDIR"

get_pid() {
    pgrep -f "bot_single.py $1" | head -1
}

start_bot() {
    local login="$1"
    if [ -n "$(get_pid "$login")" ]; then
        echo "[$login] Already running (PID: $(get_pid "$login"))"
        return
    fi
    HEADLESS=1 nohup "$PYTHON" "$SCRIPT" "$login" >> "$LOGDIR/$login.log" 2>&1 &
    echo "[$login] Started (PID: $!)"
}

stop_bot() {
    local login="$1"
    local pid=$(get_pid "$login")
    if [ -z "$pid" ]; then
        echo "[$login] Not running"
        return
    fi
    kill "$pid"
    echo "[$login] Stopped (PID: $pid)"
}

case "$1" in
    start_all)
        logins=$(python3 -c "import json; d=json.load(open('$DIR/accounts.json')); [print(a['grepolis_login']) for a in d['accounts']]")
        for login in $logins; do
            start_bot "$login"
            sleep 10
        done
        echo "All bots started."
        ;;
    stop_all)
        pkill -f "bot_single.py"
        echo "All bots stopped."
        ;;
    start)
        start_bot "$2"
        ;;
    stop)
        stop_bot "$2"
        ;;
    restart)
        stop_bot "$2"
        sleep 3
        start_bot "$2"
        ;;
    status)
        logins=$(python3 -c "import json; d=json.load(open('$DIR/accounts.json')); [print(a['grepolis_login']) for a in d['accounts']]")
        for login in $logins; do
            pid=$(get_pid "$login")
            if [ -n "$pid" ]; then
                echo "[$login] RUNNING (PID: $pid)"
            else
                echo "[$login] STOPPED"
            fi
        done
        ;;
    *)
        echo "Usage: $0 {start_all|stop_all|start <login>|stop <login>|restart <login>|status}"
        ;;
esac
