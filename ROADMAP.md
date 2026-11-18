# VibeCore Roadmap & Implementation Milestones

Author & Lead Architect: **Sandeep Hipparagi** ([@Sandeep-Hipparagi](https://github.com/Sandeep-Hipparagi))

VibeCore (`Context-Intuition`) is an open-source, local-first runtime for proactive personal agent swarms with zero-trust guardrails.

---

## 🎯 Release Milestones

```mermaid
flowchart LR
    M1["Phase 1: Hardening & Release Hygiene"] --> M2["Phase 2: Closed Tier-2 HITL Loop"]
    M2 --> M3["Phase 3: MV3 Browser Workflows"]
    M3 --> M4["Phase 4: Live Local Connectors"]
    M4 --> M5["Phase 5: Live Dashboard & Launch"]
```

### ✅ Milestone 1 (v0.1.0-alpha) — Repository Hardening & Architecture Core
- [x] Portable, path-sanitized monorepo structure.
- [x] Longitudinal Context Knowledge Graph schema (`ContextNode`, `ContextEdge`, temporal decay).
- [x] Zero-Trust Policy Enclave schema & deterministic classification (`TIER_0`, `TIER_1`, `TIER_2`).
- [x] Ephemeral handshake token verification for local WebSocket bridges.
- [x] Edit-Delta Learning engine prototype (semantic diffing of user edits into `BehavioralRule`).

### ✅ Milestone 2 (v0.1.0-beta) — Closed-Loop Tier-2 Execution & Idempotency
- [x] Tool dispatch registry mapping approved action envelopes to local executors.
- [x] Idempotent post-approval execution pipeline with atomic state locking.
- [x] Persistent trigger of `EditDeltaEngine` when actions are resolved as `EDITED`.
- [x] End-to-end integration test suite (`tests/test_tier2_lifecycle.py`).

### ✅ Milestone 3 (v0.1.0-rc1) — Authenticated MV3 Extension & Browser Workflows
- [x] Compiled Manifest V3 Chrome Extension bundle (`dist/`).
- [x] Authenticated WhatsApp Web co-pilot with DOM `MutationObserver`.
- [x] Background grocery and e-commerce price drop tracker (`TIER_0` scrape, `TIER_1` cart staging).
- [x] Rate-limited Instagram saved post archivist into `ContextGraph`.
- [x] Extension popup UI displaying connection and token status.

### ✅ Milestone 4 (v0.1.0) — Live Connectors & Overnight Chief-of-Staff
- [x] Live arXiv API (`cs.AI`, `cs.LG`, `cs.CL`) and GitHub trending scouts with trajectory filtering.
- [x] Desktop OAuth Gmail and Google Calendar connector with offline mock fallback.
- [x] Multimodal video clipping agent with `ffmpeg` scene and energy boundary detection.
- [x] Overnight scheduler daemon running the morning brief pipeline between 02:00 and 06:30.

### ✅ Milestone 5 (v0.1.0-release) — Live Dashboard & Open-Source Launch
- [x] React + Vite + Tailwind personal dashboard with live WebSocket telemetry.
- [x] Interactive Tier-2 approval queue with risk gauges, diff preview, and inline payload editor.
- [x] Context Knowledge Graph and Behavioral Rules visual inspector.
- [x] Cross-platform developer automation (`Makefile`, `scripts/dev.ps1`, `scripts/dev.sh`).
- [x] Clean, sanitized public release repository ready for GitHub distribution.

---

## 🔮 Future Community Milestones

### 🚀 Milestone 6 (v0.2.0) — Native Desktop Sidecars & Local Model Routing
- [ ] Native system tray sidecar (Tauri / Rust) for passive CPU/GPU usage throttling.
- [ ] Native macOS & Windows Notification Center integration for high-urgency Tier-2 approvals.
- [ ] Dynamic multi-model routing (speculative decoding with small draft models on Ollama + large local models).
- [ ] Local vector index expansion using DuckDB + `vss` or SQLite-vec.

### 🌐 Milestone 7 (v0.3.0) — Cross-Device P2P Sync & Mobile Companion
- [ ] End-to-end encrypted (E2EE) peer-to-peer sync between desktop and laptop instances via WebRTC / Tailscale.
- [ ] Lightweight mobile companion app (PWA / React Native) for one-click mobile biometric approvals of Tier-2 actions.
- [ ] Community MCP server registry with automated policy sandbox evaluation.
