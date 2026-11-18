#!/usr/bin/env bash
# VibeCore Developer Automation for Linux/macOS
# Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

set -e

ACTION=${1:-dev}

setup() {
    echo "Setting up VibeCore development environment..."
    python3 -m pip install -e ".[dev]"
    echo "Installing Chrome Extension dependencies..."
    (cd chrome-extension && npm install)
    echo "Installing Dashboard UI dependencies..."
    (cd dashboard-ui && npm install)
    echo "Setup complete!"
}

build_ext() {
    echo "Building Chrome Extension (Manifest V3)..."
    (cd chrome-extension && npm run build)
    echo "Chrome Extension compiled into chrome-extension/dist/"
}

run_tests() {
    echo "Running Python pytest suite..."
    pytest -v
    echo "Verifying Chrome Extension build..."
    (cd chrome-extension && npm run build)
    echo "Verifying Dashboard UI build..."
    (cd dashboard-ui && npm run build)
    echo "All tests and builds passed!"
}

run_dev() {
    echo "=========================================================="
    echo "  Starting VibeCore Swarm Runtime & Live Dashboard"
    echo "  Author: Sandeep Hipparagi"
    echo "=========================================================="

    python3 -c "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd() / 'core-orchestrator')); from app.config import get_or_create_bridge_token; print('Active Bridge Token: ' + get_or_create_bridge_token())"

    echo "Launching Orchestrator (:8000)..."
    python3 -m uvicorn app.main:app --app-dir core-orchestrator --reload --port 8000 &
    ORCH_PID=$!

    trap "kill $ORCH_PID" EXIT INT TERM

    echo "Launching Dashboard UI (:5173)..."
    (cd dashboard-ui && npm run dev)
}

case "$ACTION" in
    setup)
        setup
        ;;
    build-ext)
        build_ext
        ;;
    test)
        run_tests
        ;;
    dev)
        run_dev
        ;;
    *)
        echo "Usage: ./scripts/dev.sh [setup|dev|test|build-ext]"
        exit 1
        ;;
esac
