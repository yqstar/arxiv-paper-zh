#!/usr/bin/env bash
set -euo pipefail

SKILL_NAME="arxiv-paper-zh"
REPO_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SKILL_DIR="$REPO_DIR/skills/$SKILL_NAME"
PROJECT_DIR=""
INSTALL_CODEX=0
INSTALL_CLAUDE=0
INSTALL_AGENTS=0

usage() {
  printf '%s\n' \
    "Usage: ./install.sh [--all] [--codex] [--claude] [--agents] [--project PATH]" \
    "" \
    "  --all           Install for Codex, Claude Code, and generic Agent Skills" \
    "  --codex         Install for Codex" \
    "  --claude        Install for Claude Code" \
    "  --agents        Install in the generic .agents/skills directory" \
    "  --project PATH  Install at project scope instead of user scope" \
    "  -h, --help      Show this help"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --all)
      INSTALL_CODEX=1
      INSTALL_CLAUDE=1
      INSTALL_AGENTS=1
      ;;
    --codex) INSTALL_CODEX=1 ;;
    --claude) INSTALL_CLAUDE=1 ;;
    --agents) INSTALL_AGENTS=1 ;;
    --project)
      shift
      if [ "$#" -eq 0 ]; then
        printf 'error: --project requires a path\n' >&2
        exit 2
      fi
      PROJECT_DIR="$(cd -- "$1" && pwd)"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ "$INSTALL_CODEX" -eq 0 ] && [ "$INSTALL_CLAUDE" -eq 0 ] && [ "$INSTALL_AGENTS" -eq 0 ]; then
  INSTALL_CODEX=1
  INSTALL_CLAUDE=1
fi

install_link() {
  destination="$1"
  parent="$(dirname -- "$destination")"
  mkdir -p -- "$parent"

  if [ -L "$destination" ]; then
    current="$(readlink "$destination")"
    if [ "$current" = "$SKILL_DIR" ]; then
      printf 'already installed: %s\n' "$destination"
      return
    fi
  fi

  if [ -e "$destination" ] || [ -L "$destination" ]; then
    printf 'error: destination already exists: %s\n' "$destination" >&2
    printf 'remove or rename it manually, then run this installer again.\n' >&2
    exit 1
  fi

  ln -s -- "$SKILL_DIR" "$destination"
  printf 'installed: %s -> %s\n' "$destination" "$SKILL_DIR"
}

if [ -n "$PROJECT_DIR" ]; then
  if [ "$INSTALL_CODEX" -eq 1 ]; then
    install_link "$PROJECT_DIR/.agents/skills/$SKILL_NAME"
  fi
  if [ "$INSTALL_CLAUDE" -eq 1 ]; then
    install_link "$PROJECT_DIR/.claude/skills/$SKILL_NAME"
  fi
  if [ "$INSTALL_AGENTS" -eq 1 ]; then
    install_link "$PROJECT_DIR/.agents/skills/$SKILL_NAME"
  fi
else
  if [ "$INSTALL_CODEX" -eq 1 ]; then
    install_link "${CODEX_HOME:-$HOME/.codex}/skills/$SKILL_NAME"
  fi
  if [ "$INSTALL_CLAUDE" -eq 1 ]; then
    install_link "$HOME/.claude/skills/$SKILL_NAME"
  fi
  if [ "$INSTALL_AGENTS" -eq 1 ]; then
    install_link "$HOME/.agents/skills/$SKILL_NAME"
  fi
fi

printf '%s\n' "Installation complete. Start a new agent session if the skill is not detected immediately."
