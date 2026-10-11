#!/bin/sh
# UID 50045: trusted minimal host-loopback TCP ingress; no CDP, DB or owner
# socket mounts, and no credentials. Untrusted frontend remains network:none.
# As network_mode:host, UID50045 still has host CDP TCP reachability if
# compromised. This distinct trusted ingress remains an independent P1 gate.
set -eu
umask 077
[ "$#" -eq 0 ] || { printf '%s\n' 'mark-api: unexpected ingress arguments' >&2; exit 64; }
# Direct Compose and --no-deps must never bypass release attestation.
printf '%s\n' 'mark-api: public dashboard ingress live deployment not attested' >&2
exit 1
exec /opt/mark-api/venv/bin/python -I -m mark_api.dashboard_relay \
    public-ingress --frontend-socket /run/mark-api-ingress/frontend.sock \
    --frontend-uid 50042 --port 8875