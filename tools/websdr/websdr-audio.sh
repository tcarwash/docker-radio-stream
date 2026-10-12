#!/usr/bin/env bash
# Deliberately do not start/recreate a consumer or launch a host browser.
printf '%s\n' 'The host WebSDR helper is retired. Configure the receiver URL range map, then run from your Compose project:' 'docker compose exec -T radio python3 /opt/radio/session.py --minutes 5' >&2
exit 2
