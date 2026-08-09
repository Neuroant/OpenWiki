#!/usr/bin/env sh
# Launch the local, wiki-aware OpenCode "openwiki" agent from the repo root.
# Any extra args are passed through to opencode, e.g.:  ./start-opencode.sh run "Was ist SST?"
# Note: on macOS/Linux, change the MCP command in opencode.json from
#       ".venv/Scripts/python.exe" to ".venv/bin/python".
set -eu
cd "$(dirname "$0")"

command -v opencode >/dev/null 2>&1 || { echo 'opencode not found on PATH. Install it: https://opencode.ai/docs/' >&2; exit 1; }

if [ ! -x .venv/bin/python ] && [ ! -x .venv/Scripts/python.exe ]; then
    echo '.venv missing. Create it:  python -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"' >&2
    exit 1
fi

[ -e output/graph ] || echo 'warning: output/graph missing - graph tools unavailable (run: openwiki graph-build ...)' >&2
curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 || echo 'warning: Ollama not reachable at http://localhost:11434 (run: ollama serve)' >&2

# Isolate this instance from your global OpenCode setup (~/.config/opencode: the
# oh-my-opencode plugin and its agents) by pointing XDG_CONFIG_HOME at an empty,
# project-local dir - so ONLY this project's config and the `openwiki` agent load.
# --pure additionally disables any plugins. Run `opencode` directly (not this
# script) if you want your global OpenCode setup instead.
export XDG_CONFIG_HOME="$(pwd)/.opencode-home"
mkdir -p "$XDG_CONFIG_HOME"
exec opencode --pure "$@"
