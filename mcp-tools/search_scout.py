"""
arXiv & GitHub Trending Scout with Signal-to-Trajectory Filtering.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Fetches real-time academic papers and trending open-source projects via HTTP APIs,
scoring each item against active user trajectories and goals from ContextGraph.
Provides seamless offline fallback when network is unavailable or offline mode is requested.
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from enclave import enforce_policy
from app.schemas import TierLevel, ContextNode, ContextNodeType, TechRadarItem

logger = logging.getLogger("SearchScout")

ARXIV_API_URL = "https://export.arxiv.org/api/query"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"


class SearchScoutTool:
    """
    Scouts arXiv and GitHub for recent research and code breakthroughs,
    filtering noise against longitudinal user trajectories.
    """

    @staticmethod
    @enforce_policy(tool_name="arxiv_fetch_papers", tier=TierLevel.TIER_0_OBSERVE)
    async def fetch_recent_papers(
        categories: List[str] = ["cs.AI", "cs.LG", "cs.CL"],
        max_results: int = 8,
        offline: bool = False,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """
        Fetches recently published preprints from arXiv API, parsing Atom XML feeds.
        Falls back to realistic offline fixtures if offline=True or network is unavailable.
        """
        if offline or os.environ.get("VIBECORE_OFFLINE_MODE") == "1":
            logger.info("Using offline fixture feed for arXiv papers.")
            return SearchScoutTool._arxiv_offline_fixtures()

        query_cat = "+OR+".join([f"cat:{cat}" for cat in categories])
        params = {
            "search_query": query_cat,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": str(max_results),
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.get(ARXIV_API_URL, params=params)
                if response.status_code == 200:
                    papers = SearchScoutTool._parse_arxiv_xml(response.text)
                    if papers:
                        return papers
        except Exception as e:
            logger.warning(f"Live arXiv API request failed ({e}). Falling back to offline fixtures.")

        return SearchScoutTool._arxiv_offline_fixtures()

    @staticmethod
    @enforce_policy(tool_name="github_scan_trending", tier=TierLevel.TIER_0_OBSERVE)
    async def scan_trending(
        topics: List[str] = ["ai-agents", "local-first", "llm"],
        max_results: int = 8,
        offline: bool = False,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """
        Scans GitHub for active repositories created or pushed within the last 7 days.
        """
        if offline or os.environ.get("VIBECORE_OFFLINE_MODE") == "1":
            logger.info("Using offline fixture feed for GitHub trending repositories.")
            return SearchScoutTool._github_offline_fixtures()

        since_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        query = f"created:>{since_date} stars:>10 " + " ".join(topics)

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "VibeCore-Scout/0.1.0",
        }
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": str(max_results),
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.get(GITHUB_SEARCH_URL, headers=headers, params=params)
                if response.status_code == 200:
                    data = response.json()
                    items = data.get("items", [])
                    if items:
                        return [
                            {
                                "repo": item.get("full_name"),
                                "url": item.get("html_url"),
                                "description": item.get("description") or "No description provided",
                                "stars_today": item.get("stargazers_count", 0),
                                "language": item.get("language") or "General",
                                "topics": item.get("topics", []),
                            }
                            for item in items
                        ]
        except Exception as e:
            logger.warning(f"Live GitHub API search failed ({e}). Falling back to offline fixtures.")

        return SearchScoutTool._github_offline_fixtures()

    # ========================================================================
    # Signal-to-Trajectory Scoring
    # ========================================================================

    @staticmethod
    def filter_signal_to_trajectory(
        raw_papers: List[Dict[str, Any]],
        raw_repos: List[Dict[str, Any]],
        trajectories: List[ContextNode],
    ) -> List[TechRadarItem]:
        """
        Evaluates relevance scores between raw scout items and user trajectories.
        Filters out low-leverage items and attaches explicit 'why it matters' rationales.
        """
        scored_items: List[Tuple[float, TechRadarItem]] = []

        # Build trajectory keyword universe
        trajectory_keywords: Dict[str, List[str]] = {
            "Longitudinal Memory": ["memory", "consolidation", "temporal", "graph", "decay", "sleep", "longitudinal"],
            "Zero-Trust Guardrails": ["guardrail", "policy", "security", "zero-trust", "enclave", "injection", "hitl", "safety"],
            "Local Inference": ["local", "ollama", "vllm", "quantization", "llama.cpp", "serving", "offline", "edge"],
            "Agent Swarms": ["swarm", "multi-agent", "orchestration", "mcp", "co-pilot", "tool-use"],
        }

        # Also incorporate user-specific trajectory content
        for t in trajectories:
            summary = str(t.content.get("summary", "")).lower()
            if "memory" in summary:
                trajectory_keywords["Longitudinal Memory"].append(t.title.lower())
            if "local" in summary:
                trajectory_keywords["Local Inference"].append(t.title.lower())

        # Score arXiv papers
        for paper in raw_papers:
            text = f"{paper.get('title', '')} {paper.get('summary', '')}".lower()
            score, tag, rationale = SearchScoutTool._score_text(text, trajectory_keywords, paper.get("title", ""))
            if score >= 0.35:
                item = TechRadarItem(
                    title=paper.get("title", ""),
                    url=paper.get("url", ""),
                    source="arxiv",
                    relevance_rationale=rationale,
                    trajectory_tag=tag,
                )
                scored_items.append((score, item))

        # Score GitHub repos
        for repo in raw_repos:
            text = f"{repo.get('repo', '')} {repo.get('description', '')} {' '.join(repo.get('topics', []))}".lower()
            score, tag, rationale = SearchScoutTool._score_text(text, trajectory_keywords, repo.get("repo", ""))
            if score >= 0.30:
                item = TechRadarItem(
                    title=repo.get("repo", ""),
                    url=repo.get("url", ""),
                    source="github",
                    relevance_rationale=rationale,
                    trajectory_tag=tag,
                )
                scored_items.append((score, item))

        # Sort descending by score
        scored_items.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored_items[:6]]

    @staticmethod
    def _score_text(
        text: str,
        trajectory_keywords: Dict[str, List[str]],
        item_title: str,
    ) -> Tuple[float, str, str]:
        best_score = 0.0
        best_tag = "General AI"
        best_rationale = "General relevant open-source development."

        for tag, keywords in trajectory_keywords.items():
            matches = sum(1 for kw in keywords if kw.lower() in text)
            if matches > 0:
                score = min(1.0, 0.30 + (matches * 0.15))
                if score > best_score:
                    best_score = score
                    best_tag = tag
                    best_rationale = (
                        f"Directly matches active trajectory on '{tag}' "
                        f"with {matches} key conceptual overlap(s)."
                    )

        return best_score, best_tag, best_rationale

    # ========================================================================
    # XML Parsing & Offline Fallbacks
    # ========================================================================

    @staticmethod
    def _parse_arxiv_xml(xml_content: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        try:
            root = ET.fromstring(xml_content)
            namespace = {"atom": "http://www.w3.org/2005/Atom"}

            for entry in root.findall("atom:entry", namespace):
                title = entry.find("atom:title", namespace)
                summary = entry.find("atom:summary", namespace)
                id_el = entry.find("atom:id", namespace)
                published = entry.find("atom:published", namespace)

                author_els = entry.findall("atom:author", namespace)
                authors = [
                    a.find("atom:name", namespace).text.strip()
                    for a in author_els
                    if a.find("atom:name", namespace) is not None
                ]

                clean_title = re.sub(r"\s+", " ", title.text or "").strip()
                clean_summary = re.sub(r"\s+", " ", summary.text or "").strip()
                url = id_el.text.strip() if id_el is not None and id_el.text else ""

                if clean_title:
                    entries.append({
                        "title": clean_title,
                        "authors": authors,
                        "url": url,
                        "summary": clean_summary,
                        "published": published.text if published is not None else "",
                    })
        except Exception as e:
            logger.warning(f"Error parsing arXiv XML: {e}")
        return entries

    @staticmethod
    def _arxiv_offline_fixtures() -> List[Dict[str, Any]]:
        return [
            {
                "title": "Longitudinal Memory Consolidation in Autonomous Agent Swarms",
                "authors": ["E. Vance et al."],
                "url": "https://arxiv.org/abs/2411.09823",
                "summary": "Presents a dual-tier state ledger implementing human-like sleep cycles to prune redundant episodic memory and distill behavioral directives.",
            },
            {
                "title": "Local-First Zero-Trust Guardrails for Autonomous Browser Execution",
                "authors": ["R. Gupta et al."],
                "url": "https://arxiv.org/abs/2411.10145",
                "summary": "Demonstrates deterministic policy enclaves running inside client extension bridges to eliminate prompt injection vulnerabilities during authenticated web automation.",
            },
            {
                "title": "Hierarchical Edit-Delta Learning for Preference Induction",
                "authors": ["K. Tanaka et al."],
                "url": "https://arxiv.org/abs/2411.12001",
                "summary": "A framework for capturing user revisions to agent-staged drafts, inducing implicit policies without context window expansion.",
            },
        ]

    @staticmethod
    def _github_offline_fixtures() -> List[Dict[str, Any]]:
        return [
            {
                "repo": "vllm-project/vllm",
                "url": "https://github.com/vllm-project/vllm",
                "description": "High-throughput and memory-efficient inference and serving engine for LLMs.",
                "stars_today": 340,
                "language": "Python",
                "topics": ["local-inference", "serving", "vllm"],
            },
            {
                "repo": "modelcontextprotocol/servers",
                "url": "https://github.com/modelcontextprotocol/servers",
                "description": "Model Context Protocol reference servers and integrations.",
                "stars_today": 520,
                "language": "TypeScript",
                "topics": ["mcp", "agent-tools", "orchestration"],
            },
            {
                "repo": "ollama/ollama",
                "url": "https://github.com/ollama/ollama",
                "description": "Get up and running with Llama 3.2, Mistral, and other large language models locally.",
                "stars_today": 680,
                "language": "Go",
                "topics": ["local-first", "llm", "quantization"],
            },
        ]
