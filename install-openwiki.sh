#!/usr/bin/env sh
# Deploy the `openwiki` CLI as a GLOBAL command via pipx, in an isolated Python 3.13
# venv (Kuzu has no 3.14 wheel). Run once; then `openwiki` works from any folder and
# you create projects with `openwiki init` (no repo clone / venv per project).
#
#   ./install-openwiki.sh              # install from THIS checkout
#   ./install-openwiki.sh --git        # install from GitHub instead
#   ./install-openwiki.sh --editable   # editable install of this checkout (for hacking)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
GIT=0; EDITABLE=0
for a in "$@"; do
  case "$a" in
    --git) GIT=1 ;;
    --editable) EDITABLE=1 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

# 1. Locate Python 3.13.
py="$(python3.13 -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
[ -n "$py" ] || { echo "Python 3.13 not found (3.14 will NOT work -- no Kuzu wheel). Install python3.13." >&2; exit 1; }
echo "Using Python 3.13: $py"

# 2. Ensure pipx is available for that interpreter.
"$py" -m pipx --version >/dev/null 2>&1 || "$py" -m pip install --user --upgrade pipx

# 3. Pick the source (local checkout by default; GitHub with --git).
if [ "$GIT" -eq 1 ]; then source="git+https://github.com/Neuroant/OpenWiki.git"; else source="$here"; fi
extra=""
[ "$EDITABLE" -eq 1 ] && [ "$GIT" -eq 0 ] && extra="--editable"

# 4. Install (into pipx's own 3.13 venv; --force reinstalls if present).
echo "Installing openwiki from $source ..."
# shellcheck disable=SC2086
"$py" -m pipx install --force --python "$py" $extra "$source"

# 5. Make sure pipx's bin dir is on PATH.
"$py" -m pipx ensurepath >/dev/null 2>&1 || true

echo
echo "Done. Open a NEW terminal, then verify:  openwiki --help"
echo "Create a project anywhere:"
echo "  openwiki init my-wiki --source path/to/doc.pdf"
echo "  cd my-wiki && openwiki build && openwiki serve --port 8137"
