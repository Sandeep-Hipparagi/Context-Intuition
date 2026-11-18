# VibeCore Makefile
# Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

.PHONY: help setup dev test build-ext clean

help:
	@echo "VibeCore Developer Automation"
	@echo ""
	@echo "Targets:"
	@echo "  setup     - Install Python dependencies and npm packages"
	@echo "  dev       - Run orchestrator (:8000) and dashboard (:5173)"
	@echo "  test      - Run Python unit tests and build UI & extension"
	@echo "  build-ext - Build Chrome Extension bundle (Manifest V3)"
	@echo "  clean     - Remove build artifacts and caches"

setup:
	pip install -e ".[dev]"
	cd chrome-extension && npm install
	cd dashboard-ui && npm install

build-ext:
	cd chrome-extension && npm run build

test:
	pytest -v
	cd chrome-extension && npm run build
	cd dashboard-ui && npm run build

dev:
	python3 -c "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd() / 'core-orchestrator')); from app.config import get_or_create_bridge_token; print('Active Bridge Token: ' + get_or_create_bridge_token())"
	python3 -m uvicorn app.main:app --app-dir core-orchestrator --reload --port 8000 & \
	cd dashboard-ui && npm run dev

clean:
	rm -rf .pytest_temp .pytest_cache chrome-extension/dist dashboard-ui/dist
