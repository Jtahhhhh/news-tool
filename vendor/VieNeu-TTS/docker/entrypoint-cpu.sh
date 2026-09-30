#!/bin/sh
set -eu
mkdir -p "${HF_HOME:-/home/app/.cache/huggingface}"
chown -R app:app "${HF_HOME:-/home/app/.cache/huggingface}"
exec runuser -u app -- "$@"
