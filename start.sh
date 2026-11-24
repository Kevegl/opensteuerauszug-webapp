#!/usr/bin/env bash
# Linux-Starter: ruft start.command auf (gleiche Optionen).
exec "$(dirname "$0")/start.command" "$@"
