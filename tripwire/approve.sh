#!/bin/sh
# Fold the newest integrity report into the approved baseline.
#
# This is the missing half of the monitor. Without it the only way to clear a
# violation is `tripwire --init`, which throws the baseline away and is
# indistinguishable from tampering, so after any ordinary round of work the
# monitor is permanently red and everyone stops reading it. Approving a report
# keeps the report, so the record of what changed and when it was accepted
# survives.
#
# Run it only after reading what changed. It accepts everything in the report.
#
#   docker compose exec tripwire /approve.sh
#   docker compose exec tripwire /approve.sh --yes
set -e

LOCAL_KEY_PASSPHRASE="${LOCAL_KEY_PASSPHRASE:-AgentShieldLocalKey123!}"
HOSTNAME=$(hostname)
REPORT_DIR=/var/lib/tripwire/report
ASSUME_YES=0

if [ "${1:-}" = "--yes" ]; then
    ASSUME_YES=1
fi

# Run a check first so the numbers shown are current, and so there is a report
# to approve even if the operator has not run one recently.
if CHECK_OUTPUT=$(tripwire --check < /dev/null 2>&1); then
    STATUS=0
else
    STATUS=$?
fi

OBJECTS=$(printf '%s' "${CHECK_OUTPUT}" | sed -n 's/.*Total objects scanned:[[:space:]]*\([0-9][0-9]*\).*/\1/p')
VIOLATIONS=$(printf '%s' "${CHECK_OUTPUT}" | sed -n 's/.*Total violations found:[[:space:]]*\([0-9][0-9]*\).*/\1/p')

echo "Monitored objects: ${OBJECTS:-unknown}"
echo "Objects differing from the approved baseline: ${VIOLATIONS:-unknown}"

if [ "${STATUS}" -eq 0 ]; then
    echo "Nothing to approve, the baseline already matches the tree."
    exit 0
fi

REPORT=$(ls -1t "${REPORT_DIR}"/*.twr 2>/dev/null | head -1 || true)
if [ -z "${REPORT}" ]; then
    echo "No report found in ${REPORT_DIR}, nothing to approve."
    exit 1
fi

echo
echo "Changed objects:"
printf '%s' "${CHECK_OUTPUT}" \
    | sed -n 's/^[[:space:]]*"\(\/mnt\/[^"]*\)".*/  \1/p' \
    | sort -u
echo
echo "Report: ${REPORT}"

if [ "${ASSUME_YES}" -ne 1 ]; then
    printf 'Accept all of the above as the new approved baseline? [y/N] '
    read -r ANSWER
    case "${ANSWER}" in
        [yY]|[yY][eE][sS]) ;;
        *)
            echo "Abandoned, the baseline is unchanged."
            exit 1
            ;;
    esac
fi

# EDITOR is set to /bin/true in twcfg.txt. Update mode is interactive and shells
# out to an editor for every change, so without that the command dies with
# "Editor could not be launched" in this image and the approve path does not
# exist at all.
tripwire --update --twrfile "${REPORT}" -P "${LOCAL_KEY_PASSPHRASE}" < /dev/null

if VERIFY=$(tripwire --check < /dev/null 2>&1); then
    REMAINING=$(printf '%s' "${VERIFY}" | sed -n 's/.*Total violations found:[[:space:]]*\([0-9][0-9]*\).*/\1/p')
    echo "Approved. The baseline now matches the tree, ${REMAINING:-0} violations remaining."
else
    echo "Approved, but a follow up check still reports differences. Run tripwire --check and read the report."
    exit 1
fi
