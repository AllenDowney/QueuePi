#!/bin/sh
# Log the Pi's health once a minute, as CSV, for soak tests.
#
#     scripts/soak.sh [OUT.csv] [SECONDS]      (defaults: ~/soak.csv, 60)
#
# Columns: time; temperature and throttling; memory available; load;
# resident memory and restart count of the server and display; how long
# /healthz takes (ms, -1 if no answer); wlan0 (client Wi-Fi) state, channel, signal; hotspot
# (uap0) state and number of connected devices; whether the router and the
# internet answer a ping.
set -u
out=${1:-$HOME/soak.csv}
every=${2:-60}
IW=/usr/sbin/iw

rss() { ps -o rss= -p "$(systemctl show -p MainPID --value "$1")" 2>/dev/null | tr -d ' '; }
restarts() { systemctl show -p NRestarts --value "$1"; }
ok() { if "$@" >/dev/null 2>&1; then echo 1; else echo 0; fi; }

[ -s "$out" ] || echo "time,temp_c,throttled,mem_avail_mb,load1,server_rss_kb,server_restarts,display_rss_kb,display_restarts,healthz_ms,wlan0,wlan0_channel,wlan0_signal_dbm,hotspot,hotspot_devices,router_ping,internet_ping" > "$out"

while true; do
    t=$(date -Iseconds)
    temp=$(vcgencmd measure_temp | sed 's/temp=//; s/.C//')
    thr=$(vcgencmd get_throttled | sed 's/throttled=//')
    mem=$(awk '/MemAvailable/ {print int($2 / 1024)}' /proc/meminfo)
    load=$(cut -d' ' -f1 /proc/loadavg)
    # Milliseconds, or -1 if the server didn't answer 200.
    hz=$(curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}' http://127.0.0.1/healthz |
         awk '{if ($1 == 200) printf "%.1f", $2 * 1000; else print -1}')
    wstate=$(nmcli -t -g GENERAL.STATE device show wlan0 2>/dev/null | cut -d' ' -f1)
    wch=$($IW dev wlan0 info 2>/dev/null | awk '$1 == "channel" {print $2; exit}')
    wsig=$($IW dev wlan0 link 2>/dev/null | awk '$1 == "signal:" {print $2}')
    hstate=$(nmcli -t -g GENERAL.STATE device show uap0 2>/dev/null | cut -d' ' -f1)
    hdev=$($IW dev uap0 station dump 2>/dev/null | grep -c '^Station')
    gw=$(ip -4 route show default dev wlan0 2>/dev/null | awk '{print $3; exit}')
    rping=$( [ -n "$gw" ] && ok ping -c1 -W2 "$gw" || echo 0)
    iping=$(ok ping -c1 -W2 1.1.1.1)
    echo "$t,$temp,$thr,$mem,$load,$(rss queuepi-server),$(restarts queuepi-server),$(rss queuepi-display),$(restarts queuepi-display),$hz,$wstate,$wch,$wsig,$hstate,$hdev,$rping,$iping" >> "$out"
    sleep "$every"
done
