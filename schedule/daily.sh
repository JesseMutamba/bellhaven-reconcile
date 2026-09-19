#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p data
exec python3 -m bellhaven scan >> data/daily.log 2>&1
