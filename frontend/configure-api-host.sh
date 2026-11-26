#!/bin/sh
set -eu

api_host="${API_HOST:-api:8000}"
case "$api_host" in
  ""|*[!A-Za-z0-9.:-]*)
    echo "API_HOST must be an internal hostname with an optional port" >&2
    exit 1
    ;;
esac

sed "s|__API_HOST__|${api_host}|g" \
  /etc/nginx/default.conf.template \
  > /etc/nginx/conf.d/default.conf
