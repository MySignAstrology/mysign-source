#!/usr/bin/env bash
cd "$(dirname "$0")"
export MYSIGN_DATA="${MYSIGN_DATA:-./data}"
# Echo the sign-in code into the response instead of sending mail. Local only —
# app/auth.py refuses this on Lambda regardless of what is set here.
export MYSIGN_DEV_AUTH="${MYSIGN_DEV_AUTH:-1}"
exec python -m uvicorn app.server:app --port 8420 --host 127.0.0.1 --reload
