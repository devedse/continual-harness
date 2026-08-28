#!/usr/bin/env bash
set -e

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

GAME="red"
RUN_ID="${RUN_ID:-1}"

if [[ $# -gt 0 && "$1" != --* ]]; then
    GAME="$1"
    shift
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id)
            [[ $# -ge 2 ]] || { echo "--run-id requires a value" >&2; exit 2; }
            RUN_ID="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: ./setup_and_run.sh [red|emerald] [--run-id ID]" >&2
            exit 2
            ;;
    esac
done

RUN_ARGS=()
if [[ -n "$RUN_ID" ]]; then
    RUN_ARGS+=(--run-id "$RUN_ID")
fi

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

echo
echo "[1/6] Checking uv..."

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
else
    echo "uv already installed: $(uv --version)"
fi

echo
echo "[2/6] Checking mGBA..."

if ! ldconfig -p 2>/dev/null | grep -q libmgba; then
    echo "mGBA not found. Installing mGBA 0.10.5 for Ubuntu 24.04..."

    MGBA_INSTALL_DIR="$(mktemp -d -t continual-harness-mgba.XXXXXX)"
    trap 'rm -rf -- "$MGBA_INSTALL_DIR"' EXIT
    cd "$MGBA_INSTALL_DIR"

    echo "Downloading mGBA..."
    curl -L \
        https://github.com/mgba-emu/mgba/releases/download/0.10.5/mGBA-0.10.5-ubuntu64-noble.tar.xz \
        -o mgba.tar.xz

    echo "Extracting mGBA..."
    tar -xf mgba.tar.xz

    echo "Installing libmgba..."
    sudo apt-get update
    sudo apt-get install -y \
        ./mGBA-0.10.5-ubuntu64-noble/libmgba.deb

    sudo ldconfig

    cd "$REPO_DIR"
    rm -rf -- "$MGBA_INSTALL_DIR"
    trap - EXIT

    echo "mGBA installed."
else
    echo "mGBA already installed."
fi

echo
echo "[3/6] Installing Python dependencies..."

uv sync

echo "Python dependencies ready."

echo
echo "[4/6] Configuring local llama.cpp server..."

export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://10.88.10.1:8080/v1}"

MODEL_NAME="${MODEL_NAME:-Qwen3.8-flash-next}"

export OPENAI_API_KEY="not-needed"
export PYTHONUNBUFFERED=1

echo "LLM endpoint: $OPENAI_BASE_URL"
echo "Model: $MODEL_NAME"
echo "Game: $GAME"
if [[ -n "$RUN_ID" ]]; then
    echo "Run ID: $RUN_ID (existing checkpoints resume automatically)"
fi

echo
echo "[5/6] Choose agent mode..."
echo
echo "  1) PokeAgent"
echo "     Expert/prebuilt scaffold."
echo "     Uses the large Pokemon-specific system prompt, built-in"
echo "     strategies, tools, memory, skills, and subagents."
echo
echo "  2) Continual Harness"
echo "     Auto-improving scaffold."
echo "     Periodically analyzes its trajectory and evolves its"
echo "     prompt, memories, skills, and subagents."
echo

while true; do
    read -rp "Choose mode [1/2]: " MODE

    case "$MODE" in
        1|pokeagent|PokeAgent)
            SCAFFOLD="pokeagent"
            MODE_NAME="PokeAgent"
            break
            ;;

        2|continual|continualharness|ContinualHarness)
            SCAFFOLD="continualharness"
            MODE_NAME="Continual Harness"
            break
            ;;

        *)
            echo "Please enter 1 or 2."
            ;;
    esac
done

echo
echo "[6/6] Starting $MODE_NAME..."
echo
echo "Game:     $GAME"
echo "Model:    $MODEL_NAME"
echo "Mode:     $MODE_NAME"
echo "Scaffold: $SCAFFOLD"
echo "Web UI:   http://localhost:8000/stream"
echo

if [[ "$SCAFFOLD" == "continualharness" ]]; then

    echo "Prompt optimization: enabled"
    echo "Evolution window:    50 steps"
    echo

    exec uv run python run.py \
        --backend openai \
        --model-name "$MODEL_NAME" \
        --port 8000 \
        --agent-auto \
        --scaffold continualharness \
        --enable-prompt-optimization \
        --optimization-window-length 50 \
        --game "$GAME" \
        --direct-objectives categorized_full_game \
        --direct-objectives-start 0 \
        --direct-objectives-battling-start 0 \
        "${RUN_ARGS[@]}"

else

    exec uv run python run.py \
        --backend openai \
        --model-name "$MODEL_NAME" \
        --port 8000 \
        --agent-auto \
        --scaffold pokeagent \
        --game "$GAME" \
        --direct-objectives categorized_full_game \
        --direct-objectives-start 0 \
        --direct-objectives-battling-start 0 \
        "${RUN_ARGS[@]}"

fi
