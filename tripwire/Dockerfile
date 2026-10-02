FROM debian:bookworm-slim

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    tripwire \
    ca-certificates \
    curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /etc/tripwire

# The source config and policy go to /etc/tripwire-src, not /etc/tripwire.
#
# The tripwire-keys volume is mounted at /etc/tripwire, and a named volume
# shadows whatever the image put at that path, keeping the copy it made when the
# volume was first created. Copying the config there therefore pinned whatever
# twcfg.txt and twpol.txt said the first time the volume was created: editing
# them on the host changed nothing for any deployment that already had a volume,
# and it did so silently while every command reported success. The keys still
# live in the volume, which is the point of that volume.
COPY twcfg.txt /etc/tripwire-src/twcfg.txt
COPY twpol.txt /etc/tripwire-src/twpol.txt
COPY entrypoint.sh /entrypoint.sh
COPY approve.sh /approve.sh

RUN chmod +x /entrypoint.sh /approve.sh

ENTRYPOINT ["/entrypoint.sh"]
