#!/bin/sh
set -eu

unset KEJTU_NATIVE_PROFILE KEJTU_NATIVE_NOTES_WRITE KEJTU_NATIVE_STANDING_PROFILE
case "${1:-}" in
  initialize-provider|reopen-provider|initialize-ollama|reopen-ollama|initialize-deepseek|reopen-deepseek)
    [ "$#" -ge 2 ] || { echo "Provider mode requires an absolute model profile path" >&2; exit 2; }
    case "$2" in /*) ;; *) echo "Profile path must be absolute" >&2; exit 2 ;; esac
    [ -f "$2" ] && [ -r "$2" ] || { echo "Profile unavailable" >&2; exit 2; }
    export KEJTU_NATIVE_PROFILE="$2"
    unset KEJTU_R013_INITIALIZE KEJTU_R013_SYNTHETIC_NOTES VITE_R013_SMOKE
    case "$1" in initialize-*) export KEJTU_R013_INITIALIZE=1 ;; esac
    shift 2
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --notes-write)
          [ -z "${KEJTU_NATIVE_NOTES_WRITE:-}" ] || { echo "Repeated note option" >&2; exit 2; }
          export KEJTU_NATIVE_NOTES_WRITE=1
          shift ;;
        --standing-profile)
          [ "$#" -ge 2 ] && [ -z "${KEJTU_NATIVE_STANDING_PROFILE:-}" ] || { echo "Standing profile requires one path" >&2; exit 2; }
          case "$2" in /*) ;; *) echo "Standing profile path must be absolute" >&2; exit 2 ;; esac
          [ -f "$2" ] && [ -r "$2" ] || { echo "Standing profile unavailable" >&2; exit 2; }
          export KEJTU_NATIVE_STANDING_PROFILE="$2"
          shift 2 ;;
        *) echo "Unknown provider option" >&2; exit 2 ;;
      esac
    done ;;
  initialize) export KEJTU_R013_INITIALIZE=1; unset KEJTU_R013_SYNTHETIC_NOTES VITE_R013_SMOKE ;;
  reopen) unset KEJTU_R013_INITIALIZE KEJTU_R013_SYNTHETIC_NOTES VITE_R013_SMOKE ;;
  initialize-notes) export KEJTU_R013_INITIALIZE=1 KEJTU_R013_SYNTHETIC_NOTES=1; unset VITE_R013_SMOKE ;;
  reopen-notes) export KEJTU_R013_SYNTHETIC_NOTES=1; unset KEJTU_R013_INITIALIZE VITE_R013_SMOKE ;;
  smoke-reopen) unset KEJTU_R013_INITIALIZE KEJTU_R013_SYNTHETIC_NOTES; export VITE_R013_SMOKE=1 ;;
  *) echo "usage: ./run-development.sh initialize|reopen|initialize-notes|reopen-notes|smoke-reopen | initialize-provider|reopen-provider|initialize-ollama|reopen-ollama|initialize-deepseek|reopen-deepseek /absolute/profile.json [--notes-write] [--standing-profile /absolute/profile.md]" >&2; exit 2 ;;
esac
if [ -z "${KEJTU_NATIVE_PROFILE:-}" ] && [ "$#" -ne 1 ]; then
  echo "Fake mode does not accept provider options" >&2
  exit 2
fi

candidate_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -x "$candidate_dir/.venv/bin/python" ] || { echo "Install this checkout's Python environment first; see README.md" >&2; exit 2; }
# Never inherit a build destination belonging to a different development tree.
CARGO_TARGET_DIR="$candidate_dir/.state/build/rust"
export CARGO_TARGET_DIR
GDK_BACKEND=${GDK_BACKEND:-x11}
WINIT_UNIX_BACKEND=${WINIT_UNIX_BACKEND:-x11}
WEBKIT_DISABLE_DMABUF_RENDERER=${WEBKIT_DISABLE_DMABUF_RENDERER:-1}
export GDK_BACKEND WINIT_UNIX_BACKEND WEBKIT_DISABLE_DMABUF_RENDERER
unset WAYLAND_DISPLAY
cd "$candidate_dir/desktop"
exec "$candidate_dir/.venv/bin/python" -B "$candidate_dir/tools/linux_toolchain.py" launch
