#!/bin/sh
# Container entrypoint: make the document storage writable, then run the
# command as the unprivileged "app" user.
#
# Hosts such as Render mount persistent disks owned by root, which the app
# user could not write to. When started as root, this fixes the ownership
# of STORAGE_DIR and drops privileges; when already running as app (for
# example `docker run --user app`), it just runs the command.
set -eu

if [ "$(id -u)" = "0" ]; then
    storage="${STORAGE_DIR:-/app/storage}"
    mkdir -p "$storage"
    chown app:app "$storage"
    # setpriv keeps the environment: point HOME at the app user's home, or
    # libraries that read ~ (asyncpg looks for ~/.postgresql client certs)
    # hit /root and fail with "permission denied".
    HOME=/app exec setpriv --reuid=app --regid=app --init-groups -- "$0" "$@"
fi

exec "$@"
