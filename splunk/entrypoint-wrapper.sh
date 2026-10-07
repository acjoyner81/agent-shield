#!/bin/bash
#
# Runs before /sbin/entrypoint.sh, so everything here has to finish quickly and
# fail loudly.
#
# The previous version of this file ran its certificate generator in a background
# subshell `( ... ) &`. That made every failure in it invisible: openssl errored,
# the wrapper reported success, Splunk came up with no collector certificate, and
# the telemetry pipeline was silently broken with nothing in any log to say so.
# It also encrypted the private key with the literal `pass:password`.
#
# What this script is responsible for, in order:
#   1. establish a certificate authority for the collector and publish its public
#      certificate to a shared volume, as a single file, so the gateway and the
#      aggregator can verify the collector without any signing key being readable
#      outside this container
#   2. issue a collector certificate that actually names the host the other
#      containers use, because Splunk's own generated certificate has no subject
#      alternative names at all and therefore cannot verify for any hostname
#   3. hand that certificate to the image's own Splunk provisioning, which is the
#      step that actually enables the collector
#
# Why the authority is issued here rather than reused from Splunk: Splunk's CA
# private key at /opt/splunk/etc/auth/ca.pem is an encrypted PKCS#12 blob that
# opens with neither SPLUNK_PASSWORD, nor "changeme", nor splunk.secret, so it
# cannot be used to sign anything from inside the container. Measured on
# splunk/splunk 10.4.3; see docs/specs/0014-land-telemetry-in-splunk/rationale.md.
set -euo pipefail

AUTH_DIR=/opt/splunk/etc/auth
SHARE_DIR=/opt/splunk/ca-share
ANSIBLE_DEFAULTS=/opt/ansible/inventory/splunk_defaults_linux.yml
CA_PEM="${AUTH_DIR}/agentshield_collector_ca.pem"
CA_CRT="${AUTH_DIR}/agentshield_collector_ca.crt"
HEC_PEM="${AUTH_DIR}/hec_server.pem"
ISSUED_MARKER="${AUTH_DIR}/.hec_issued"
WORK_DIR=/tmp/splunk_hec_ssl

# The bundled openssl needs Splunk's own libraries to load at all, and sudo
# resets the environment by default, so every invocation goes through
# `sudo env LD_LIBRARY_PATH=...` rather than relying on an exported variable.
OPENSSL_LD=/opt/splunk/lib
OPENSSL=/opt/splunk/bin/openssl

log() { printf '[Splunk Wrapper] %s\n' "$*"; }
die() { printf '[Splunk Wrapper] FATAL: %s\n' "$*" >&2; exit 1; }

[ -n "${SPLUNK_PASSWORD:-}" ] || die "SPLUNK_PASSWORD is not set; refusing to start."

# The leaf's passphrase has to be stable across recreates, because Splunk's
# provisioning re-posts it to the collector on every boot and a different
# passphrase than the stored key was encrypted with breaks a healthy volume.
# Deriving it from the environment keeps it stable and keeps it out of the
# repository; a passphrase rotation is detected by the marker below and forces
# the leaf to be reissued.
HEC_PASSPHRASE="${SPLUNK_HEC_CERT_PASSWORD:-$SPLUNK_PASSWORD}"
[ -n "$HEC_PASSPHRASE" ] || die "the collector certificate passphrase resolved to an empty string."

# On a fresh etc volume the auth directory does not exist yet: Splunk creates it
# during its own bootstrap, which has not run at this point. Everything below
# writes into it, so it is created here first.
sudo mkdir -p "$AUTH_DIR"

# ---------------------------------------------------------------------------
# 1. Certificate authority
#
# Lives in the persisted etc volume so it survives a recreate, and only its
# public half is copied to the shared volume. The signing key never leaves this
# container and is never mounted into another service.
# ---------------------------------------------------------------------------
if sudo test -f "$CA_PEM"; then
  log "reusing the existing collector certificate authority"
else
  log "creating the collector certificate authority"
  sudo rm -rf "$WORK_DIR"
  sudo mkdir -p "$WORK_DIR"

  sudo tee "${WORK_DIR}/ca.cnf" >/dev/null <<'CNF'
[req]
distinguished_name = req_dn
prompt = no
x509_extensions = v3_ca

[req_dn]
CN = AgentShield Collector CA

[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
CNF

  # The config is explicit because the bundled openssl carries a build time
  # OPENSSLDIR that does not exist in this image, so `req` fails outright
  # without one. The signing key is encrypted rather than left in the clear,
  # and the passphrase arrives on stdin.
  printf '%s' "$SPLUNK_PASSWORD" | sudo env LD_LIBRARY_PATH="$OPENSSL_LD" "$OPENSSL" req -x509 \
    -newkey rsa:4096 \
    -config "${WORK_DIR}/ca.cnf" \
    -keyout "${WORK_DIR}/ca.key" \
    -out "${WORK_DIR}/ca.crt" \
    -days 3650 \
    -sha256 \
    -passout stdin
  sudo sh -c "cat '${WORK_DIR}/ca.crt' '${WORK_DIR}/ca.key' > '${CA_PEM}'"
  sudo cp -f "${WORK_DIR}/ca.crt" "$CA_CRT"
  sudo chown splunk:splunk "$CA_PEM" "$CA_CRT"
  sudo chmod 600 "$CA_PEM"
  sudo chmod 644 "$CA_CRT"
  sudo rm -rf "$WORK_DIR"
fi
sudo test -f "$CA_CRT" || die "the collector certificate authority is missing its public certificate at ${CA_CRT}."

sudo mkdir -p "$SHARE_DIR"
sudo cp -f "$CA_CRT" "${SHARE_DIR}/ca.crt"
sudo chmod 644 "${SHARE_DIR}/ca.crt"
log "published the collector trust anchor to ${SHARE_DIR}/ca.crt"

# ---------------------------------------------------------------------------
# 2. Collector certificate
#
# Reissued when it is missing or when either the authority or the passphrase it
# was issued under has changed, so the chain and the decryption passphrase can
# never drift apart across recreates.
# ---------------------------------------------------------------------------
CA_FINGERPRINT="$(sudo env LD_LIBRARY_PATH="$OPENSSL_LD" "$OPENSSL" x509 -in "$CA_CRT" -noout -fingerprint -sha256)"
EXPECTED_MARKER="${CA_FINGERPRINT} $(printf '%s' "$HEC_PASSPHRASE" | sha256sum)"

REISSUE=0
sudo test -f "$HEC_PEM" || REISSUE=1
if sudo test -f "$ISSUED_MARKER"; then
  if [ "$(sudo cat "$ISSUED_MARKER")" != "$EXPECTED_MARKER" ]; then
    REISSUE=1
  fi
else
  REISSUE=1
fi

if [ "$REISSUE" = "1" ]; then
  log "issuing the collector certificate (CN=splunk, SAN splunk/localhost/127.0.0.1)"
  sudo rm -rf "$WORK_DIR"
  sudo mkdir -p "$WORK_DIR"

  sudo tee "${WORK_DIR}/san.cnf" >/dev/null <<'CNF'
[req]
distinguished_name = req_dn
prompt = no

[req_dn]
CN = splunk

[v3_req]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = splunk
DNS.2 = localhost
IP.1 = 127.0.0.1
CNF

  sudo env LD_LIBRARY_PATH="$OPENSSL_LD" "$OPENSSL" genrsa -out "${WORK_DIR}/hec_server.key" 2048
  sudo env LD_LIBRARY_PATH="$OPENSSL_LD" "$OPENSSL" req -new \
    -key "${WORK_DIR}/hec_server.key" \
    -out "${WORK_DIR}/hec_server.csr" \
    -config "${WORK_DIR}/san.cnf"

  # Both passphrases arrive on stdin rather than in an argument or an
  # environment variable, so neither is visible in the process table and neither
  # depends on sudo being willing to pass variables through.
  printf '%s' "$HEC_PASSPHRASE" | sudo env LD_LIBRARY_PATH="$OPENSSL_LD" "$OPENSSL" x509 -req \
    -in "${WORK_DIR}/hec_server.csr" \
    -CA "$CA_CRT" \
    -CAkey "$CA_PEM" \
    -passin stdin \
    -CAcreateserial \
    -out "${WORK_DIR}/hec_server.crt" \
    -days 1095 \
    -sha256 \
    -extfile "${WORK_DIR}/san.cnf" \
    -extensions v3_req

  printf '%s' "$HEC_PASSPHRASE" | sudo env LD_LIBRARY_PATH="$OPENSSL_LD" "$OPENSSL" rsa \
    -in "${WORK_DIR}/hec_server.key" \
    -aes256 \
    -out "${WORK_DIR}/hec_server.enc.key" \
    -passout stdin

  # Splunk's server certificate format: leaf, encrypted private key, then the
  # issuing authority, concatenated in that order.
  sudo sh -c "cat '${WORK_DIR}/hec_server.crt' '${WORK_DIR}/hec_server.enc.key' '${CA_CRT}' > '${HEC_PEM}'"
  sudo chown splunk:splunk "$HEC_PEM"
  sudo chmod 600 "$HEC_PEM"

  printf '%s' "$EXPECTED_MARKER" | sudo tee "$ISSUED_MARKER" >/dev/null
  sudo chown splunk:splunk "$ISSUED_MARKER"
  sudo chmod 600 "$ISSUED_MARKER"
  sudo rm -rf "$WORK_DIR"
  log "collector certificate issued and installed at ${HEC_PEM}"
else
  log "reusing the existing collector certificate at ${HEC_PEM}"
fi

# ---------------------------------------------------------------------------
# 3. Hand the certificate to Splunk's provisioning
#
# The image reads its own defaults from this one file, and its `hec` block ships
# with `cert:` and `password:` empty. Splunk's provisioning posts those two values
# to the collector, so empty means the collector is enabled with TLS on and no
# certificate, which serves Splunk's generated default. That default has no
# subject alternative names, so it cannot verify for any hostname, including
# `splunk`.
#
# There is no environment variable for these two keys, so the file is patched.
# The patch is a scoped line edit rather than a YAML round trip: a round trip
# would rewrite the whole file, drop its ordering, and make an image upgrade show
# up as an unexplained diff. Values are JSON quoted so a passphrase containing `#`
# or `:` cannot change the meaning of the line.
# ---------------------------------------------------------------------------
log "pointing the collector at the issued certificate in the Splunk defaults"
sudo chmod u+w "$ANSIBLE_DEFAULTS"
sudo ANSIBLE_DEFAULTS="$ANSIBLE_DEFAULTS" \
     HEC_PEM="$HEC_PEM" \
     HEC_PASSPHRASE="$HEC_PASSPHRASE" \
     /usr/bin/python3 <<'PY'
import json
import os
import sys

path = os.environ["ANSIBLE_DEFAULTS"]
lines = open(path).read().splitlines()
wanted = {"cert": os.environ["HEC_PEM"], "password": os.environ["HEC_PASSPHRASE"]}
patched = {}

out = []
i = 0
while i < len(lines):
    line = lines[i]
    out.append(line)
    if line.strip() != "hec:":
        i += 1
        continue

    block_indent = len(line) - len(line.lstrip())
    i += 1
    while i < len(lines):
        nxt = lines[i]
        if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= block_indent:
            break
        key = nxt.strip().split(":", 1)[0]
        if key in wanted and key not in patched:
            out.append(" " * (block_indent + 4) + key + ": " + json.dumps(wanted[key]))
            patched[key] = True
            i += 1
            continue
        out.append(nxt)
        i += 1

missing = sorted(set(wanted) - set(patched))
if missing:
    sys.exit("the Splunk defaults hec block has no key(s): %s" % ", ".join(missing))

open(path, "w").write("\n".join(out) + "\n")
print("[Splunk Wrapper] patched hec.cert and hec.password in %s" % path)
PY

# Execute official Splunk entrypoint
if [ $# -eq 0 ]; then
  set -- start-service
fi
exec /sbin/entrypoint.sh "$@"