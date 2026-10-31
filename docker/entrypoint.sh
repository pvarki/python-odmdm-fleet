#!/bin/bash -l
set -e
if [ "$#" -eq 0 ]; then
  exec odmdm-fleet --help
fi
exec "$@"
