"""
Tests for Live arXiv/GitHub Scouts, Signal-to-Trajectory Scoring,
Google Workspace offline fallback, FFmpeg clipping, and Morning Brief API.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

from pathlib import Path

import httpx
import pytest
from app.main import app
from app.schemas import ContextNode, ContextNodeType
from ffmpeg_tool import FFmpegClippingTool
from google_workspace import GoogleWorkspaceConnector
from search_scout import SearchScoutTool

SAMPLE_ARXIV_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2411.99999v1</id>
    <published>2026-11-18T12:00:00Z</published>
    <title> Autonomous Memory Consolidation with Temporal Knowledge Graphs </title>
    <summary> We propose a dual-tier agent architecture combining sleep-phase graph pruning
    with inductive preference learning from human edit deltas. </summary>
    <author>
      <name>Alice Smith</name>
    </author>
    <author>
      <name>Bob Jones</name>
    </author>
  </entry>
</feed>
"""


def test_arxiv_xml_parsing_and_scoring():
    # 1. Test XML parsing
    parsed = SearchScoutTool._parse_arxiv_xml(SAMPLE_ARXIV_XML)
    assert len(parsed) == 1
    item = parsed[0]
    assert item["title"] == "Autonomous Memory Consolidation with Temporal Knowledge Graphs"
    assert "Alice Smith" in item["authors"]
    assert "Bob Jones" in item["authors"]
    assert item["url"] == "http://arxiv.org/abs/2411.99999v1"

    # 2. Test Signal-to-Trajectory scoring
    traj = ContextNode(
        node_id="traj_1",
        node_type=ContextNodeType.TRAJECTORY,
        title="Agent Memory Consolidation",
        content={"summary": "Building sleep decay and longitudinal memory for agents."},
        source="test",
    )
    radar = SearchScoutTool.filter_signal_to_trajectory(
        raw_papers=parsed,
        raw_repos=[],
        trajectories=[traj],
    )
    assert len(radar) == 1
    assert radar[0].trajectory_tag == "Longitudinal Memory"
    assert "key conceptual overlap" in radar[0].relevance_rationale


def test_github_offline_fixtures_and_scoring():
    repos = SearchScoutTool._github_offline_fixtures()
    assert len(repos) >= 2

    traj = ContextNode(
        node_id="traj_2",
        node_type=ContextNodeType.TRAJECTORY,
        title="Local Inference",
        content={"summary": "Deploying Ollama and vLLM models offline."},
        source="test",
    )
    radar = SearchScoutTool.filter_signal_to_trajectory(
        raw_papers=[],
        raw_repos=repos,
        trajectories=[traj],
    )
    assert len(radar) > 0
    assert any(r.source == "github" for r in radar)


def test_google_workspace_offline_fallback():
    connector = GoogleWorkspaceConnector()
    # Without real OAuth credentials in test environment, must run in offline mode
    assert connector.is_live is False


@pytest.mark.asyncio
async def test_ffmpeg_clipping_tool_staging():
    # Test cutting a video highlight
    result = await FFmpegClippingTool.cut_highlight(
        input_path="nonexistent_test_raw.mp4",
        start_time=10.0,
        end_time=25.0,
        aspect_ratio="9:16",
        clip_name="test_highlight_clip",
        transcript_snippet="This is a key takeaway from the architecture discussion.",
    )

    assert result["status"] == "STAGED"
    assert result["duration"] == 15.0
    assert result["aspect_ratio"] == "9:16"

    clip_path = Path(result["clip_path"])
    caption_path = Path(result["caption_path"])

    assert clip_path.exists()
    assert caption_path.exists()

    caption_text = caption_path.read_text(encoding="utf-8")
    assert "key takeaway" in caption_text


@pytest.mark.asyncio
async def test_api_get_latest_morning_brief():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/brief/latest")
        assert response.status_code == 200
        data = response.json()
        assert "brief_id" in data
        assert "executive_summary" in data
        assert len(data["commitments_detected"]) > 0
        assert len(data["tech_radar"]) > 0
