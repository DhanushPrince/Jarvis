#!/usr/bin/env bash
# stop.sh — Kill all Jarvis processes (LLM :1234, agent :7860, client :3000).
#
#   cd <repo>
#   ./scripts/stop.sh

say() { printf '\033[1;33m==> %s\033[0m\n' "$*"; }

say "Stopping Jarvis"
lsof -ti:7860 | xargs kill -9 2>/dev/null && echo "  Killed :7860 (agent)" || true
lsof -ti:1234 | xargs kill -9 2>/dev/null && echo "  Killed :1234 (LLM)"   || true
lsof -ti:3000 | xargs kill -9 2>/dev/null && echo "  Killed :3000 (client)" || true
pkill -f mlx_lm.server 2>/dev/null && echo "  Killed mlx_lm.server" || true
pkill -f "python bot.py" 2>/dev/null && echo "  Killed bot.py" || true
say "All stopped."
