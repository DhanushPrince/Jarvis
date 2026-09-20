#!/usr/bin/env sh
set -eu

here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
root="$(dirname "$here")"

if [ -x "$root/.venv/bin/python" ]; then
  python="$root/.venv/bin/python"
else
  python="${PYTHON:-python3}"
fi

exec "$python" -m pipecat.evals suite -d "$here/manifest.yaml" "$@"
