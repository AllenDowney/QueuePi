#!/bin/sh
# Start the QueuePi Wi-Fi hotspot on the Pi's built-in radio, alongside its
# normal Wi-Fi connection (wlan0). Run at boot by queuepi-hotspot.service.
#
# The Pi 4's chip can be a client and an access point at once, but only on
# one channel. So wait for wlan0 to join its network (the shop's Wi-Fi, or
# home), start the hotspot on that channel, and if wlan0 never connects,
# start it on channel 1 anyway: QueuePi must work without the shop's Wi-Fi.
#
# The hotspot itself is the NetworkManager connection "QueuePi" (AP mode,
# WPA2, ipv4.method shared = 10.42.0.1/24 with DHCP), on interface uap0.
# install.sh creates it from wifi_ssid / wifi_password in the config.
set -eu

IW=/usr/sbin/iw
CON=QueuePi
AP=uap0
WAIT=${QUEUEPI_HOTSPOT_WAIT:-30}

log() { echo "queuepi-hotspot: $*"; }

# 1. The channel wlan0 is on, once it has joined a network.
ch=""
i=0
while [ "$i" -lt "$WAIT" ]; do
    ch=$($IW dev wlan0 info 2>/dev/null | awk '$1 == "channel" {print $2; exit}')
    [ -n "$ch" ] && break
    sleep 1
    i=$((i + 1))
done
if [ -n "$ch" ]; then
    log "wlan0 is on channel $ch"
else
    ch=1
    log "wlan0 hasn't joined a network after ${WAIT}s; using channel $ch"
fi
if [ "$ch" -gt 14 ]; then
    band=a
    # 5 GHz channels 52-144 need radar detection (DFS) and can't host an
    # access point; others work but fewer phones and less range. Pin the
    # client connection to 2.4 GHz to avoid this:
    #   nmcli connection modify <wlan0 connection> 802-11-wireless.band bg
    log "WARNING: wlan0 is on 5 GHz channel $ch; the hotspot must share it." \
        "Pin wlan0's connection to 2.4 GHz (802-11-wireless.band bg)."
else
    band=bg
fi

# 2. The access-point interface, with its own (locally administered) MAC.
if ! $IW dev "$AP" info >/dev/null 2>&1; then
    $IW dev wlan0 interface add "$AP" type __ap
    mac=$(sed 's/^../02/' /sys/class/net/wlan0/address)
    ip link set "$AP" address "$mac"
    log "created $AP ($mac)"
fi

# 3. Hand it to NetworkManager and start the hotspot on wlan0's channel.
i=0
until nmcli -t -f DEVICE device | grep -qx "$AP"; do
    i=$((i + 1))
    [ "$i" -gt 20 ] && { log "NetworkManager doesn't see $AP"; exit 1; }
    sleep 0.5
done
nmcli device set "$AP" managed yes
nmcli connection modify "$CON" 802-11-wireless.channel "$ch" 802-11-wireless.band "$band"
nmcli connection up "$CON"
log "hotspot $CON up on channel $ch"
