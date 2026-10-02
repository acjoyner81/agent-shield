#!/bin/sh
set -e

SITE_KEY_PASSPHRASE="${SITE_KEY_PASSPHRASE:-AgentShieldSiteKey123!}"
LOCAL_KEY_PASSPHRASE="${LOCAL_KEY_PASSPHRASE:-AgentShieldLocalKey123!}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-300}"

HOSTNAME=$(hostname)

echo "[Tripwire FIM] Initializing Tripwire for host: ${HOSTNAME}"

# Ensure directories exist
mkdir -p /etc/tripwire /var/lib/tripwire /var/lib/tripwire/report

# The policy and config are read straight out of the image, from
# /etc/tripwire-src, and are never copied into /etc/tripwire.
#
# An earlier version copied them into /etc/tripwire but only when the source was
# newer, on the theory that the volume is a place an operator might customise.
# It is not, and the guard was actively harmful: /etc/tripwire is the
# tripwire-keys volume, which persists, so a stale or edited copy there outlived
# every rebuild. A policy that had been tampered with on the volume survived a
# rebuild from the corrected repo and kept being enforced, while the image
# carried the right one and nothing said so. A security tool must not keep a
# writable second copy of its own rules. The volume is for keys and compiled
# artifacts; the rules come from the repo.
TWCFG_SRC=/etc/tripwire-src/twcfg.txt
TWPOL_SRC=/etc/tripwire-src/twpol.txt

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

    # --print-cfgfile echoes the unresolved template, so $(HOSTNAME) is still literal
    # in its output. An earlier check compared that template against the expanded path,
    # so the two could never agree and every start declared the config stale and
    # rebuilt it. Expand the template first, then ask the question that actually
    # matters: does the baseline this config names exist for this host?
    RESOLVED=$(printf '%s' "${COMPILED}" | sed "s/\$(HOSTNAME)/${HOSTNAME}/g")
    case "${RESOLVED}" in
        "DBFILE=/var/lib/tripwire/${HOSTNAME}.twd")
            if [ ! -f "/var/lib/tripwire/${HOSTNAME}.twd" ]; then
                echo "[Tripwire FIM] Compiled config names a baseline that is missing, recompiling..."
                NEED_CFG=1
            fi
            ;;
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
        "${TWCFG_SRC}" < /dev/null
fi

# Compile Policy File
#
# Staleness is decided by checksum, not by timestamp. Timestamps go wrong in both
# directions: an image rebuild refreshes the source's mtime without changing its
# content, which would recompile the policy on every deploy, while a copy edited
# on the volume could carry an mtime old enough to look current. Recording the
# checksum of the source at compile time and comparing it on start up asks the
# question that actually matters, which is whether the compiled policy was built
# from these exact rules.
#
# Existence alone was never enough. An edited policy silently kept enforcing the
# old rules while every command reported success, which for a security control is
# the worst kind of bug, because the only evidence is that a change you made
# quietly did nothing.
#
# A recompiled policy invalidates the baseline, because the policy decides what
# is monitored at all. That is deliberately NOT handled by rebuilding the
# baseline here. An automatic rebuild would be convenient and wrong: it converts
# a policy change into a fresh baseline built from whatever the policy now says,
# so editing twpol.txt to drop a path from scope and restarting would silently
# rebuild the monitor around the reduced scope and then report itself clean. The
# loud failure Tripwire already gives, "Policy file does not match policy used to
# create database", is the correct behaviour, because changing what the monitor
# watches has to be a decision a human makes on purpose. See
# TRIPWIRE_REBUILD_BASELINE below.
POLICY_RECOMPILED=0
POLICY_STALE=0
if [ ! -f /etc/tripwire/tw.pol ]; then
    POLICY_STALE=1
elif [ ! -f /etc/tripwire/twpol.sha256 ]; then
    # No recorded checksum to compare against, so there is no evidence the rules
    # actually changed. Recompile so tw.pol matches the image, but do not claim
    # the policy changed and do not expect the baseline to be invalidated: Tripwire
    # compares the compiled policy against the database itself and refuses on its
    # own if they really do differ, which is the check that matters. Claiming a
    # change here would cry wolf on the first run against an existing volume.
    POLICY_STALE=1
elif ! sha256sum -c /etc/tripwire/twpol.sha256 --status < /dev/null 2>&1; then
    POLICY_STALE=1
    POLICY_RECOMPILED=1
fi

if [ "${POLICY_STALE}" -eq 1 ]; then
    echo "[Tripwire FIM] Signing policy file..."
    twadmin --create-polfile \
        --site-keyfile /etc/tripwire/site.key \
        -Q "${SITE_KEY_PASSPHRASE}" \
        "${TWPOL_SRC}" < /dev/null
    sha256sum "${TWPOL_SRC}" > /etc/tripwire/twpol.sha256
    POLICY_RECOMPILED=1
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
# A report reads "Database last updated on: Never" until the first approve, and
# that is Tripwire 2.4.3.7 behaving normally rather than a broken baseline. Only
# --update stamps the field; --init never does, so a brand new deployment shows
# "Never" on its first report even though the database was just written. Verified
# against a database whose file mtime was minutes old. It turns into a real date
# the first time /approve.sh folds a reviewed report into the baseline.
# Rebuilding the baseline is always an explicit, one shot operator action.
#
#   docker compose run --rm -e TRIPWIRE_REBUILD_BASELINE=1 tripwire
#
# It exits straight after rather than entering the monitoring loop, so the command
# does what it says and returns.
if [ "${TRIPWIRE_REBUILD_BASELINE:-0}" = "1" ]; then
    echo "[Tripwire FIM] Baseline rebuild requested, rebuilding against the current policy..."
    tripwire --init -P "${LOCAL_KEY_PASSPHRASE}" < /dev/null
    echo "[Tripwire FIM] Baseline database rebuilt. Run approve.sh to fold in any reviewed changes."
    exit 0
fi

if [ "${POLICY_RECOMPILED}" -eq 1 ]; then
    echo ""
    echo "[Tripwire FIM] ======================================================================="
    echo "[Tripwire FIM] THE POLICY CHANGED, SO THE EXISTING BASELINE IS NO LONGER VALID."
    echo "[Tripwire FIM] It has deliberately NOT been rebuilt automatically. Every check below"
    echo "[Tripwire FIM] will refuse to run until someone decides what to do, because"
    echo "[Tripwire FIM] rebuilding it here would silently re-scope the monitor to whatever"
    echo "[Tripwire FIM] twpol.txt now says and then report itself clean."
    echo "[Tripwire FIM]"
    echo "[Tripwire FIM] If you changed twpol.txt on purpose:"
    echo "[Tripwire FIM]   docker compose run --rm -e TRIPWIRE_REBUILD_BASELINE=1 tripwire"
    echo "[Tripwire FIM] If you did not, treat it as tampering. twpol.txt is watched by the"
    echo "[Tripwire FIM] monitor's own SEC_SELF rule, and the previous version is in git."
    echo "[Tripwire FIM] ======================================================================="
    echo ""
fi

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
    if CHECK_OUTPUT=$(tripwire --check < /dev/null 2>&1); then
        STATUS=0
    else
        STATUS=$?
    fi
    printf '%s\n' "${CHECK_OUTPUT}"

    OBJECTS=$(printf '%s' "${CHECK_OUTPUT}" | sed -n 's/.*Total objects scanned:[[:space:]]*\([0-9][0-9]*\).*/\1/p')
    VIOLATIONS=$(printf '%s' "${CHECK_OUTPUT}" | sed -n 's/.*Total violations found:[[:space:]]*\([0-9][0-9]*\).*/\1/p')

    # Tripwire refuses to check at all when the policy changed underneath the
    # baseline. That is a different failure from "files changed", and it does not
    # scan a single object, so OBJECTS and VIOLATIONS come back empty. Reporting
    # that as "0 of 0 monitored objects differ" hides both the cause and the part
    # that matters most, which is that nothing was verified at all.
    POLICY_MISMATCH=0
    if printf '%s' "${CHECK_OUTPUT}" | grep -q "does not match policy"; then
        POLICY_MISMATCH=1
    fi

    # Decide whether this cycle should raise an alert.
    #
    # A violation that nobody has approved yet persists across cycles, so
    # alerting unconditionally meant one row per CHECK_INTERVAL_SECONDS, all of
    # them into telemetry_history, which is a single capped list shared by every
    # tenant. Six consecutive alerts evicted six real tenant audit rows, and a
    # violation left unattended for a day would post 288 of them and push a
    # tenant's entire audit history out of the list. A monitor that destroys the
    # data it exists to protect is worse than no monitor.
    #
    # So alert on the transition into a violation, again whenever the count
    # changes because a different set of files drifted, and otherwise at most
    # once per ALERT_REPEAT_SECONDS so a long outage still keeps reminding.
    ALERT_REPEAT_SECONDS="${ALERT_REPEAT_SECONDS:-3600}"
    ALERT_STATE_FILE=/var/lib/tripwire/status/alert.state
    NOW=$(date -u +%s)
    SHOULD_ALERT=0

    if [ "${STATUS}" -ne 0 ]; then
        if [ ! -f "${ALERT_STATE_FILE}" ]; then
            SHOULD_ALERT=1
        else
            # shellcheck disable=SC1090
            . "${ALERT_STATE_FILE}"
            if [ "${LAST_STATE:-clean}" != "violations" ] || [ "${LAST_VIOLATIONS:-0}" != "${VIOLATIONS:-0}" ]; then
                SHOULD_ALERT=1
            elif [ $(( NOW - ${LAST_ALERT_AT:-0} )) -ge "${ALERT_REPEAT_SECONDS}" ]; then
                SHOULD_ALERT=1
            fi
        fi
        if [ "${SHOULD_ALERT}" -eq 1 ]; then
            if [ "${POLICY_MISMATCH}" -eq 1 ]; then
                echo "[Tripwire FIM] ALERT: the policy no longer matches the baseline, so nothing was checked!"
                curl -s -X POST http://agentshield-python-gateway:8000/v1/telemetry/logs \
                    -H "Content-Type: application/json" \
                    -d "{\"tenant_id\": \"system\", \"level\": \"CRITICAL\", \"message\": \"Tripwire FIM on ${HOSTNAME}: the policy no longer matches the baseline database, so NO objects were verified this cycle. If twpol.txt was not changed on purpose this is tampering. Rebuild deliberately with docker compose run --rm -e TRIPWIRE_REBUILD_BASELINE=1 tripwire\"}" \
                    || echo "[Tripwire FIM] Failed to send alert to gateway"
            else
                echo "[Tripwire FIM] ALERT: Integrity violation detected (exit ${STATUS})!"
                curl -s -X POST http://agentshield-python-gateway:8000/v1/telemetry/logs \
                    -H "Content-Type: application/json" \
                    -d "{\"tenant_id\": \"system\", \"level\": \"CRITICAL\", \"message\": \"Tripwire FIM on ${HOSTNAME}: ${VIOLATIONS:-?} of ${OBJECTS:-?} monitored objects differ from the approved baseline. Review the report, then run tripwire/approve.sh to accept the change.\"}" \
                    || echo "[Tripwire FIM] Failed to send alert to gateway"
            fi
        else
            echo "[Tripwire FIM] Violation unchanged since the last alert, not re-alerting (${VIOLATIONS:-?} objects)."
        fi
        mkdir -p "$(dirname "${ALERT_STATE_FILE}")"
        STAMP="${LAST_ALERT_AT:-0}"
        if [ "${SHOULD_ALERT}" -eq 1 ]; then
            STAMP="${NOW}"
        fi
        printf 'LAST_STATE=violations\nLAST_VIOLATIONS=%s\nLAST_ALERT_AT=%s\n' \
            "${VIOLATIONS:-0}" "${STAMP}" > "${ALERT_STATE_FILE}"
    else
        echo "[Tripwire FIM] No violations detected."
        rm -f "${ALERT_STATE_FILE}"
    fi

    # Publish the verdict where the gateway can read it.
    #
    # The telemetry alert above lands in the audit stream under tenant_id
    # "system", which no real tenant ever reads, so the alert was invisible to
    # every human. Platform integrity is platform health rather than tenant
    # audit data, so it belongs on the service health surface the dashboard
    # already renders. A file on a shared volume keeps that honest without
    # opening another unauthenticated write endpoint for anyone to spoof.
    #
    # Written every cycle, including the clean case, because the gateway treats
    # a missing or stale verdict as degraded. A monitor that has died must not
    # read as a healthy one.
    VERDICT_DIR=/var/lib/tripwire/status
    mkdir -p "${VERDICT_DIR}"
    REASON=""
    if [ "${POLICY_MISMATCH}" -eq 1 ]; then
        VERDICT="error"
        REASON="Policy no longer matches the baseline, so nothing was verified. Rebuild deliberately or restore the previous twpol.txt."
    elif [ "${STATUS}" -ne 0 ]; then
        VERDICT="violations"
        # No reason here on purpose. A real violation is fully described by the
        # count, which the gateway's health probe already renders, and duplicating
        # it would only risk the two disagreeing.
    else
        VERDICT="clean"
    fi
    VERDICT_TMP="${VERDICT_DIR}/verdict.json.tmp"
    printf '{"verdict":"%s","objects":%s,"violations":%s,"checked_at":"%s","host":"%s","reason":"%s"}\n' \
        "${VERDICT}" \
        "${OBJECTS:-0}" \
        "${VIOLATIONS:-0}" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "${HOSTNAME}" \
        "${REASON}" > "${VERDICT_TMP}"
    mv "${VERDICT_TMP}" "${VERDICT_DIR}/verdict.json"

    # Tripwire writes a fresh report on every check and never prunes any of them.
    # At one report per CHECK_INTERVAL_SECONDS that grows without bound and fills
    # the volume, which is what produced the "No space left on device" errors in
    # the container log. Keep the most recent REPORT_KEEP so an operator can still
    # review the current one.
    REPORT_KEEP="${REPORT_KEEP:-20}"
    if [ -d /var/lib/tripwire/report ]; then
        ls -1t /var/lib/tripwire/report/*.twr 2>/dev/null \
            | tail -n "+$((REPORT_KEEP + 1))" \
            | while read -r OLD_REPORT; do
                rm -f "${OLD_REPORT}"
            done
    fi

    echo "[Tripwire FIM] Integrity check complete. Sleeping for ${CHECK_INTERVAL_SECONDS}s..."
    sleep "${CHECK_INTERVAL_SECONDS}"
done
