#!/bin/sh
set -eu
: "${BACKEND_UPSTREAM:?Set BACKEND_UPSTREAM to backend-host:port}"
: "${TRUSTED_PROXY_CIDR:=127.0.0.1/32}"
: "${DNS_RESOLVER:=$(awk '/^nameserver / {print $2; exit}' /etc/resolv.conf)}"
export BACKEND_UPSTREAM TRUSTED_PROXY_CIDR DNS_RESOLVER
envsubst '${BACKEND_UPSTREAM} ${TRUSTED_PROXY_CIDR} ${DNS_RESOLVER}' \
    < /etc/nginx/server.conf.template > /tmp/launchpad-server.conf
nginx -t
exec "$@"
