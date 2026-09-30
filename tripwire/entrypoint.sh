#!/bin/sh
set -e

SITE_KEY_PASSPHRASE="${SITE_KEY_PASSPHRASE:-AgentShieldSiteKey123!}"
LOCAL_KEY_PASSPHRASE="${LOCAL_KEY_PASSPHRASE:-AgentShieldLocalKey123!}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-300}"

HOSTNAME=$(hostname)

echo "[Tripwire FIM] Initializing Tripwire for host: ${HOSTNAME}"

# Ensure directories exist
mkdir -p /etc/tripwire /var/lib/tripwire /var/lib/tripwire/report

# Generate Site and Local Keys if not present.
#
# twadmin prompts before overwriting an existing key, and with LATEPROMPTING=false
# on a non-TTY stdin it reprints that prompt indefinitely. Every one of those
# lines lands in the container log, so a stale key volume turned this into a
# disk-fill loop that wedged the Docker VM. Close stdin on the call so a prompt
# fails fast instead, and only generate when the site key is genuinely absent.
#
# twadmin's passphrase flags are -Q for the site key and -P for the local key.
# Passing them the other way round generated the site key under the local
# passphrase, so every later -Q call then failed with "Incorrect site
# passphrase" and the container could never finish initializing.
if [ ! -f /etc/tripwire/site.key ]; then
    echo "[Tripwire FIM] Generating site and local encryption keys..."
    twadmin --generate-keys \
        --site-keyfile /etc/tripwire/site.key \
        --local-keyfile "/etc/tripwire/${HOSTNAME}-local.key" \
        -Q "${SITE_KEY_PASSPHRASE}" \
        -P "${LOCAL_KEY_PASSPHRASE}" < /dev/null
elif [ ! -f "/etc/tripwire/${HOSTNAME}-local.key" ]; then
    # A local key from a previous container hostname is present but unusable for
    # this host. Generating one requires a new site key pair, so the stale keys
    # and the compiled config have to go before we can rebuild.
    echo "[Tripwire FIM] Local key for ${HOSTNAME} missing (stale hostname?), rebuilding keys and config..."
    rm -f /etc/tripwire/site.key /etc/tripwire/*-local.key /etc/tripwire/tw.cfg /etc/tripwire/tw.pol
    twadmin --generate-keys \
        --site-keyfile /etc/tripwire/site.key \
        --local-keyfile "/etc/tripwire/${HOSTNAME}-local.key" \
        -Q "${SITE_KEY_PASSPHRASE}" \
        -P "${LOCAL_KEY_PASSPHRASE}" < /dev/null
fi

# twadmin expands $(HOSTNAME) in twcfg.txt when it signs tw.cfg, so a config
# built under a previous container hostname points at that host's local key and
# baseline DB. tw.cfg itself is a signed binary blob, so the only way to read
# the compiled paths back is to have twadmin print it. Recompile when that
# output is not this host's, otherwise every check looks for a local key that
# does not exist.
#
# `--print-cfgfile` takes no key or passphrase flags: tw.cfg is already signed,
# so passing --site-keyfile here is an "Invalid argument" error. An earlier
# version of this check did pass it, which made every print fail and forced a
# needless rebuild on every start.
#
# Only a missing config is (re)created. An existing config for this host is
# reused, so a healthy container does not rebuild it on every start.
NEED_CFG=0
if [ ! -f /etc/tripwire/tw.cfg ]; then
    NEED_CFG=1
else
    COMPILED=$(twadmin --print-cfgfile < /dev/null 2>/dev/null | grep -m1 '^DBFILE=' || true)
    case "${COMPILED}" in
        "DBFILE=/var/lib/tripwire/${HOSTNAME}.twd") ;;
        *)
            echo "[Tripwire FIM] Compiled config is stale (${COMPILED:-unreadable}), recompiling..."
            NEED_CFG=1
            ;;
    esac
fi

if [ "${NEED_CFG}" -eq 1 ]; then
    echo "[Tripwire FIM] Signing configuration file..."
    twadmin --create-cfgfile \
        --site-keyfile /etc/tripwire/site.key \
        -Q "${SITE_KEY_PASSPHRASE}" \
        /etc/tripwire/twcfg.txt < /dev/null
fi

# Compile Policy File
if [ ! -f /etc/tripwire/tw.pol ]; then
    echo "[Tripwire FIM] Signing policy file..."
    twadmin --create-polfile \
        --site-keyfile /etc/tripwire/site.key \
        -Q "${SITE_KEY_PASSPHRASE}" \
        /etc/tripwire/twpol.txt < /dev/null
fi

# Build the baseline database if it is missing or empty.
#
# Testing only for the file's existence was not enough: a 12 KB stub can be left
# behind by a failed init, the guard passes, and tripwire then reports every
# monitored file as "Added" forever instead of comparing against a real baseline.
# A real run over the /mnt mounts scans ~250 objects, so require the database to
# be substantial before trusting it.
DB_FILE="/var/lib/tripwire/${HOSTNAME}.twd"
DB_MIN_BYTES=4096
if [ ! -f "${DB_FILE}" ] || [ "$(wc -c < "${DB_FILE}" 2>/dev/null || echo 0)" -lt "${DB_MIN_BYTES}" ]; then
    echo "[Tripwire FIM] Building initial baseline integrity database..."
    tripwire --init -P "${LOCAL_KEY_PASSPHRASE}" < /dev/null
    echo "[Tripwire FIM] Baseline database built successfully."
fi

echo "[Tripwire FIM] Starting continuous monitoring loop (check every ${CHECK_INTERVAL_SECONDS}s)..."

while true; do
    echo "[Tripwire FIM] Running integrity check at $(date -u)..."
    
    # Run the check and capture output/exit code.
    #
    # --interactive takes no argument. Passing "--interactive false" made tripwire
    # treat "false" as a path to monitor, so every run reported a bogus violation
    # for a nonexistent file and shipped a CRITICAL alert to the gateway. LATEPROMPTING=false
    # in twcfg.txt is what makes this non-interactive.
    # STATUS is captured inside an if condition on purpose: this script runs under
    # `set -e`, so a bare `tripwire --check` that returns non-zero would exit the
    # container before the exit code could be read.
    if tripwire --check < /dev/null; then
        STATUS=0
    else
        STATUS=$?
    fi

    if [ "${STATUS}" -ne 0 ]; then
        echo "[Tripwire FIM] ALERT: Integrity violation detected (exit ${STATUS})!"

        # Ship the alert to the Python Gateway
        curl -s -X POST http://agentshield-python-gateway:8000/v1/telemetry/logs \
            -H "Content-Type: application/json" \
            -d "{\"tenant_id\": \"system\", \"level\": \"CRITICAL\", \"message\": \"Tripwire FIM detected a file integrity violation on host ${HOSTNAME}!\"}" \
            || echo "[Tripwire FIM] Failed to send alert to gateway"
    else
        echo "[Tripwire FIM] No violations detected."
    fi

    echo "[Tripwire FIM] Integrity check complete. Sleeping for ${CHECK_INTERVAL_SECONDS}s..."
    sleep "${CHECK_INTERVAL_SECONDS}"
done
