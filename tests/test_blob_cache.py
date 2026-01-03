"""
Unit tests for the Blob Cache Service.

Tests cache operations, statistics, and integration with MigrationState.
"""
import pytest
from unittest.mock import MagicMock

from services.blob_cache import BlobCache, LayerInfo, LayerQueue


class TestBlobCacheBasic:
    """Tests for basic BlobCache operations."""
    
    @pytest.fixture
    def cache(self):
        return BlobCache()
    
    def test_unknown_digest(self, cache):
        """Test checking unknown digest returns None."""
        result = cache.exists("sha256:unknown")
        assert result is None
    
    def test_mark_exists(self, cache):
        """Test marking digest as existing."""
        cache.mark_exists("sha256:abc123")
        
        assert cache.exists("sha256:abc123") is True
    
    def test_mark_uploaded(self, cache):
        """Test marking digest as uploaded."""
        cache.mark_uploaded("sha256:def456")
        
        assert cache.exists("sha256:def456") is True
    
    def test_preload(self, cache):
        """Test preloading digests."""
        digests = [
            "sha256:aaa",
            "sha256:bbb",
            "sha256:ccc",
        ]
        cache.preload(digests)
        
        for digest in digests:
            assert cache.exists(digest) is True
    
    def test_clear(self, cache):
        """Test clearing cache."""
        cache.mark_exists("sha256:test")
        assert cache.exists("sha256:test") is True
        
        cache.clear()
        assert cache.exists("sha256:test") is None


class TestBlobCacheStats:
    """Tests for BlobCache statistics."""
    
    @pytest.fixture
    def cache(self):
        return BlobCache()
    
    def test_initial_stats(self, cache):
        """Test initial statistics."""
        stats = cache.stats
        
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate_percent"] == 0
    
    def test_stats_after_operations(self, cache):
        """Test statistics after cache operations."""
        # Miss
        cache.exists("sha256:miss1")
        cache.exists("sha256:miss2")
        
        # Cache an entry
        cache.mark_exists("sha256:cached")
        
        # Hit
        cache.exists("sha256:cached")
        cache.exists("sha256:cached")
        
        stats = cache.stats
        assert stats["size"] == 1
        assert stats["hits"] == 2
        assert stats["misses"] == 2
        assert stats["hit_rate_percent"] == 50.0


class TestBlobCacheWithState:
    """Tests for BlobCache with MigrationState integration."""
    
    def test_cache_with_state(self):
        """Test cache initialized with state."""
        mock_state = MagicMock()
        mock_state.known_blobs = {"sha256:preloaded"}
        mock_state.add_known_blob = MagicMock()
        
        cache = BlobCache(mock_state)
        
        # Should have preloaded blob
        assert cache.exists("sha256:preloaded") is True
    
    def test_mark_exists_updates_state(self):
        """Test mark_exists updates state."""
        mock_state = MagicMock()
        mock_state.known_blobs = set()
        
        cache = BlobCache(mock_state)
        cache.mark_exists("sha256:new")
        
        mock_state.add_known_blob.assert_called_once_with("sha256:new")


class TestLayerInfo:
    """Tests for LayerInfo dataclass."""
    
    def test_creation(self):
        """Test LayerInfo creation."""
        layer = LayerInfo(
            digest="sha256:abc",
            size=1024 * 1024 * 50,  # 50 MB
            media_type="application/vnd.docker.image.rootfs.diff.tar.gzip",
            index=0,
        )
        
        assert layer.digest == "sha256:abc"
        assert layer.size == 1024 * 1024 * 50
        assert layer.size_mb == 50.0


class TestLayerQueue:
    """Tests for LayerQueue."""
    
    def test_queue_sorts_by_size(self):
        """Test queue sorts layers by size ascending."""
        layers = [
            {"digest": "sha256:large", "size": 100_000_000, "mediaType": "test"},
            {"digest": "sha256:small", "size": 1_000, "mediaType": "test"},
            {"digest": "sha256:medium", "size": 10_000_000, "mediaType": "test"},
        ]
        
        queue = LayerQueue(layers)
        layer_list = list(queue)
        
        assert layer_list[0].digest == "sha256:small"
        assert layer_list[1].digest == "sha256:medium"
        assert layer_list[2].digest == "sha256:large"
    
    def test_queue_length(self):
        """Test queue length."""
        layers = [
            {"digest": "sha256:a", "size": 100},
            {"digest": "sha256:b", "size": 200},
        ]
        
        queue = LayerQueue(layers)
        assert len(queue) == 2
    
    def test_total_size(self):
        """Test total size calculation."""
        layers = [
            {"digest": "sha256:a", "size": 1000},
            {"digest": "sha256:b", "size": 2000},
            {"digest": "sha256:c", "size": 3000},
        ]
        
        queue = LayerQueue(layers)
        assert queue.total_size == 6000
        assert queue.total_size_mb == 6000 / (1024 * 1024)
