#!/bin/sh
# Certbot deploy hook: new certificate files take effect after a valid reload.
set -eu
nginx -t
systemctl reload nginx
