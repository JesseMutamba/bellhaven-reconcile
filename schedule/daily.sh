#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")/.."
mkdir -p data
exec python3 -m bellhaven scan >> data/daily.log 2>&1
