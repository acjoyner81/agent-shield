FROM debian:bookworm-slim

RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    tripwire \
    ca-certificates \
    curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /etc/tripwire

COPY twcfg.txt /etc/tripwire/twcfg.txt
COPY twpol.txt /etc/tripwire/twpol.txt
COPY entrypoint.sh /entrypoint.sh
COPY approve.sh /approve.sh

RUN chmod +x /entrypoint.sh /approve.sh

ENTRYPOINT ["/entrypoint.sh"]
