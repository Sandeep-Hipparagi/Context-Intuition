"""
Local Hybrid Semantic + Temporal Search Engine.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Provides local embedding generation via Ollama with deterministic offline fallback,
and hybrid semantic + temporal ranking for ContextKnowledgeGraph.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np

from app.config import settings

logger = logging.getLogger("VectorIndex")


@dataclass
class EmbeddingResult:
    """Result of embedding generation."""
    vector: List[float]
    model: str
    dimension: int
    source: str  # "ollama" or "offline"


class LocalEmbeddingEngine:
    """
    Local embedding provider with Ollama primary and deterministic offline fallback.
    """

    def __init__(
        self,
        ollama_url: Optional[str] = None,
        model: Optional[str] = None,
        mock_mode: bool = False,
        dimension: int = 256,
    ):
        self.ollama_url = ollama_url or settings.OLLAMA_URL.replace("/api/chat", "/api/embeddings")
        self.model = model or os.environ.get("VIBECORE_EMBED_MODEL", "nomic-embed-text")
        self.mock_mode = mock_mode or settings.MOCK_LLM
        self.dimension = dimension
        self._client: Optional[httpx.AsyncClient] = None
        self._offline_cache: Dict[str, List[float]] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def embed(self, text: str) -> EmbeddingResult:
        """
        Generate embedding for text. Tries Ollama first, falls back to deterministic offline.
        """
        if self.mock_mode:
            return self._offline_embed(text)

        try:
            result = await self._ollama_embed(text)
            return result
        except Exception as e:
            logger.warning(f"Ollama embedding failed: {e}. Falling back to offline mode.")
            return self._offline_embed(text)

    async def embed_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """Generate embeddings for multiple texts."""
        if self.mock_mode:
            return [self._offline_embed(t) for t in texts]

        try:
            return await self._ollama_embed_batch(texts)
        except Exception as e:
            logger.warning(f"Ollama batch embedding failed: {e}. Falling back to offline mode.")
            return [self._offline_embed(t) for t in texts]

    async def _ollama_embed(self, text: str) -> EmbeddingResult:
        """Call Ollama /api/embeddings endpoint."""
        payload = {
            "model": self.model,
            "prompt": text,
        }
        response = await self.client.post(self.ollama_url, json=payload)
        response.raise_for_status()
        data = response.json()
        vector = data.get("embedding", [])
        if not vector:
            raise ValueError("Empty embedding returned from Ollama")
        # Normalize to unit vector
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = (np.array(vector) / norm).tolist()
        return EmbeddingResult(
            vector=vector,
            model=self.model,
            dimension=len(vector),
            source="ollama",
        )

    async def _ollama_embed_batch(self, texts: List[str]) -> List[EmbeddingResult]:
        """Call Ollama /api/embeddings for multiple texts (sequential for simplicity)."""
        results = []
        for text in texts:
            results.append(await self._ollama_embed(text))
        return results

    def _offline_embed(self, text: str) -> EmbeddingResult:
        """
        Deterministic offline embedding using feature hashing (N-gram + character n-grams).
        Produces a stable, normalized unit vector for the same input text.
        """
        cache_key = hashlib.sha256(text.encode()).hexdigest()[:32]
        if cache_key in self._offline_cache:
            return EmbeddingResult(
                vector=self._offline_cache[cache_key],
                model="offline-deterministic",
                dimension=self.dimension,
                source="offline",
            )

        # Generate feature vector using character n-grams and word hashing
        vector = self._feature_hash_embed(text)
        self._offline_cache[cache_key] = vector

        return EmbeddingResult(
            vector=vector,
            model="offline-deterministic",
            dimension=self.dimension,
            source="offline",
        )

    def _feature_hash_embed(self, text: str) -> List[float]:
        """
        Deterministic feature hashing embedding.
        Uses character 3-grams and word-level hashing for stability.
        """
        text = text.lower().strip()
        if not text:
            return [0.0] * self.dimension

        # Feature extraction: character 3-grams + words
        features = []
        
        # Character 3-grams
        for i in range(len(text) - 2):
            features.append(text[i:i+3])
        
        # Word-level features
        words = re.findall(r'\b\w+\b', text)
        features.extend(words)
        
        # Hash each feature to a position in the vector
        vector = np.zeros(self.dimension, dtype=np.float32)
        for feature in features:
            # Deterministic hash to index
            h = int(hashlib.sha256(feature.encode()).hexdigest(), 16)
            idx = h % self.dimension
            # Use another hash for the value
            h2 = int(hashlib.sha256((feature + "_val").encode()).hexdigest(), 16)
            val = ((h2 % 10000) / 10000.0) * 2 - 1  # Range [-1, 1]
            vector[idx] += val

        # Normalize to unit vector
        norm = np.linalg.norm(vector)
        if norm > 0:
            vector = vector / norm
        
        return vector.tolist()

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two vectors (clamped to [0, 1])."""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        v1 = np.array(vec1)
        v2 = np.array(vec2)
        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        sim = float(np.dot(v1, v2) / (norm1 * norm2))
        return max(0.0, min(1.0, sim))  # Clamp to [0, 1]


# Global embedding engine instance
_global_embedding_engine: Optional[LocalEmbeddingEngine] = None


def get_embedding_engine() -> LocalEmbeddingEngine:
    global _global_embedding_engine
    if _global_embedding_engine is None:
        _global_embedding_engine = LocalEmbeddingEngine()
    return _global_embedding_engine


async def shutdown_embedding_engine():
    global _global_embedding_engine
    if _global_embedding_engine:
        await _global_embedding_engine.close()
        _global_embedding_engine = None