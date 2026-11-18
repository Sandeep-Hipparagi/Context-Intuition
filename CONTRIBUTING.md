# Contributing to VibeCore

Thank you for your interest in contributing to VibeCore (`Context-Intuition`)! This project is maintained by **Sandeep Hipparagi** and the open-source community.

## Core Tenets
1. **Local-First & Privacy Preserving:** Sensitive personal data, credentials, and DOM states must never be transmitted off the host machine without explicit cryptographic user authorization.
2. **Zero-Trust Policy Boundaries:** Every agent tool execution modifying system state, financial resources, or external communication channels must adhere to `TIER_2_COMMIT` human signoff guardrails.
3. **Deterministic Staging:** Staging actions (`TIER_1_STAGE`) must be completely reversible and decoupled from execution triggers.

## Development Workflow
1. Fork and clone the repository.
2. Set up the Python virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: .\venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   ```
3. Run tests before submitting a PR:
   ```bash
   pytest
   ```
4. Verify code formatting:
   ```bash
   ruff check .
   ```

## Pull Request Guidelines
- Ensure all tests pass.
- Maintain strict typing (`mypy` compliant).
- Document new tools and schema changes in `docs/` and `schemas.py`.
