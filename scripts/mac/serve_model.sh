#!/bin/bash
# Keep the model server running on 127.0.0.1 (launchd: at login, restarted if it stops).
# Serves the training run named in data/ai/SERVING (one line: a run folder) if that file
# exists, else [llm] model from config.local.ini. REITSTREET_SERVE_RUN overrides both.
source "$(dirname "$0")/common.sh"
RUN="${REITSTREET_SERVE_RUN:-}"
if [ -z "$RUN" ] && [ -f "$ROOT/data/ai/SERVING" ]; then
  RUN="$(head -n 1 "$ROOT/data/ai/SERVING")"
fi
if [ -n "$RUN" ]; then
  exec reitstreet-ai serve --run "$RUN"
fi
exec reitstreet-ai serve
