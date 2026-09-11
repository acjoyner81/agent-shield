#!/bin/sh
set -e

SITE_KEY_PASSPHRASE="${SITE_KEY_PASSPHRASE:-AgentShieldSiteKey123!}"
LOCAL_KEY_PASSPHRASE="${LOCAL_KEY_PASSPHRASE:-AgentShieldLocalKey123!}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-300}"

HOSTNAME=$(hostname)

echo "[Tripwire FIM] Initializing Tripwire for host: ${HOSTNAME}"

# Ensure directories exist
mkdir -p /etc/tripwire /var/lib/tripwire /var/lib/tripwire/report

# Generate Site and Local Keys if not present
if [ ! -f /etc/tripwire/site.key ]; then
    echo "[Tripwire FIM] Generating site and local encryption keys..."
    twadmin --generate-keys \
        --site-keyfile /etc/tripwire/site.key \
        --local-keyfile "/etc/tripwire/${HOSTNAME}-local.key" \
        -P "${SITE_KEY_PASSPHRASE}" \
        -p "${LOCAL_KEY_PASSPHRASE}"
fi

# Compile Configuration File
if [ ! -f /etc/tripwire/tw.cfg ]; then
    echo "[Tripwire FIM] Signing configuration file..."
    twadmin --create-cfgfile \
        --site-keyfile /etc/tripwire/site.key \
        -Q "${SITE_KEY_PASSPHRASE}" \
        /etc/tripwire/twcfg.txt
fi

# Compile Policy File
if [ ! -f /etc/tripwire/tw.pol ]; then
    echo "[Tripwire FIM] Signing policy file..."
    twadmin --create-polfile \
        --site-keyfile /etc/tripwire/site.key \
        -Q "${SITE_KEY_PASSPHRASE}" \
        /etc/tripwire/twpol.txt
fi

# Initialize Database if not present
DB_FILE="/var/lib/tripwire/${HOSTNAME}.twd"
if [ ! -f "${DB_FILE}" ]; then
    echo "[Tripwire FIM] Building initial baseline integrity database..."
    tripwire --init -P "${LOCAL_KEY_PASSPHRASE}"
    echo "[Tripwire FIM] Baseline database built successfully."
fi

echo "[Tripwire FIM] Starting continuous monitoring loop (check every ${CHECK_INTERVAL_SECONDS}s)..."

while true; do
    echo "[Tripwire FIM] Running integrity check at $(date -u)..."
    tripwire --check --interactive false || true
    echo "[Tripwire FIM] Integrity check complete. Sleeping for ${CHECK_INTERVAL_SECONDS}s..."
    sleep "${CHECK_INTERVAL_SECONDS}"
done
