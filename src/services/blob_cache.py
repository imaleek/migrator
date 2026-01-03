"""
Blob Cache Service.

Provides in-memory caching for blob existence checks to avoid
redundant HEAD requests to the registry. Particularly useful for:
- Base image layers shared across many images
- Config blobs that are often duplicated
- Resume scenarios where blobs were already verified
"""
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from utilities.logger import get_logger

if TYPE_CHECKING:
    from services.migration_state import MigrationState

logger = get_logger(__name__)


class BlobCache:
    """
    In-memory cache for blob existence with optional state persistence.
    
    The cache tracks three states for each digest:
    - EXISTS: Blob is known to exist in destination
    - UPLOADED: Blob was uploaded in this session
    - UNKNOWN: Not in cache (need to check registry)
    
    This dramatically reduces HEAD requests for common base layers.
    """
    
    def __init__(self, state: "MigrationState | None" = None):
        """
        Initialize the blob cache.
        
        Args:
            state: Optional migration state for persistence
        """
        self._state = state
        self._local_cache: set[str] = set()
        self._hits = 0
        self._misses = 0
        
        # Pre-populate from state if available
        if state:
            self._local_cache.update(state.known_blobs)
            if state.known_blobs:
                logger.debug(f"Loaded {len(state.known_blobs)} cached blob digests from state")
    
    def exists(self, digest: str) -> bool | None:
        """
        Check if a blob is known to exist.
        
        Args:
            digest: Blob digest (sha256:...)
        
        Returns:
            True if known to exist, None if unknown (requires registry check)
        """
        if digest in self._local_cache:
            self._hits += 1
            return True
        self._misses += 1
        return None
    
    def mark_exists(self, digest: str) -> None:
        """
        Mark a blob as existing in the destination.
        
        Call this after verifying blob exists via HEAD request.
        
        Args:
            digest: Blob digest
        """
        self._local_cache.add(digest)
        if self._state:
            self._state.add_known_blob(digest)
    
    def mark_uploaded(self, digest: str) -> None:
        """
        Mark a blob as uploaded in this session.
        
        This also marks it as existing since we just uploaded it.
        
        Args:
            digest: Blob digest
        """
        self.mark_exists(digest)
        logger.debug(f"Cached uploaded blob: {digest[:16]}...")
    
    def preload(self, digests: list[str]) -> None:
        """
        Preload known digests into the cache.
        
        Useful when resuming a migration or loading from external state.
        
        Args:
            digests: List of blob digests
        """
        count = 0
        for digest in digests:
            if digest not in self._local_cache:
                self._local_cache.add(digest)
                count += 1
        if count > 0:
            logger.debug(f"Preloaded {count} blob digests into cache")
    
    @property
    def stats(self) -> dict[str, int]:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "size": len(self._local_cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 1),
        }
    
    def log_stats(self) -> None:
        """Log cache performance statistics."""
        stats = self.stats
        if stats["hits"] + stats["misses"] > 0:
            logger.info(
                f"Blob cache stats: {stats['size']} entries, "
                f"{stats['hits']} hits, {stats['misses']} misses, "
                f"{stats['hit_rate_percent']}% hit rate"
            )
    
    def clear(self) -> None:
        """Clear the cache."""
        self._local_cache.clear()
        self._hits = 0
        self._misses = 0


@dataclass
class LayerInfo:
    """Information about a layer for parallel processing."""
    digest: str
    size: int
    media_type: str
    index: int  # Position in layer list
    
    @property
    def size_mb(self) -> float:
        """Get size in megabytes."""
        return self.size / (1024 * 1024)


class LayerQueue:
    """
    Priority queue for layer transfers.
    
    Prioritizes smaller layers to maximize parallelism efficiency
    and provide faster progress feedback.
    """
    
    def __init__(self, layers: list[dict]):
        """
        Initialize with layer dictionaries from manifest.
        
        Args:
            layers: List of layer dicts with 'digest', 'size', 'mediaType' keys
        """
        self._layers = [
            LayerInfo(
                digest=layer["digest"],
                size=layer.get("size", 0),
                media_type=layer.get("mediaType", "application/octet-stream"),
                index=i
            )
            for i, layer in enumerate(layers)
        ]
        # Sort by size ascending (small layers first for better parallelism)
        self._layers.sort(key=lambda x: x.size)
        self._index = 0
    
    def __iter__(self):
        return self
    
    def __next__(self) -> LayerInfo:
        if self._index >= len(self._layers):
            raise StopIteration
        layer = self._layers[self._index]
        self._index += 1
        return layer
    
    def __len__(self) -> int:
        return len(self._layers)
    
    @property
    def total_size(self) -> int:
        """Total size of all layers."""
        return sum(l.size for l in self._layers)
    
    @property
    def total_size_mb(self) -> float:
        """Total size in megabytes."""
        return self.total_size / (1024 * 1024)
