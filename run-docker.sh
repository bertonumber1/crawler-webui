#!/bin/sh
set -eu

if [ ! -f .env.docker ]; then
  cp .env.docker.example .env.docker
  echo "Created .env.docker from .env.docker.example. Check the paths/settings, then run again."
  exit 1
fi

mkdir -p data
exec docker compose "$@"
