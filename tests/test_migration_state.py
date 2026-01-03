"""
Unit tests for the Migration State Service.

Tests state persistence, resume capability, and session management.
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import shutil

from services.migration_state import MigrationState, DEFAULT_STATE_DIR


class TestMigrationStateCreation:
    """Tests for MigrationState creation."""
    
    def test_create_new_state(self):
        """Test creating a new migration state."""
        state = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            source_namespace="project-a",
            dest_namespace="project-b",
        )
        
        assert state.session_id is not None
        assert state.source_registry == "source.io"
        assert state.dest_registry == "dest.io"
        assert state.source_namespace == "project-a"
        assert state.dest_namespace == "project-b"
        assert len(state.completed_items) == 0
        assert len(state.failed_items) == 0
    
    def test_create_new_with_state_dir(self, tmp_path):
        """Test creating state with a state directory."""
        state = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path,
        )
        
        assert state._state_file is not None
        assert state._state_file.parent == tmp_path


class TestMigrationStateItems:
    """Tests for item tracking in MigrationState."""
    
    @pytest.fixture
    def state(self):
        return MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
        )
    
    def test_item_key_generation(self, state):
        """Test item key generation."""
        key = state.item_key("image", "myrepo:v1.0")
        assert key == "image:myrepo:v1.0"
    
    def test_mark_completed(self, state):
        """Test marking item as completed."""
        state.mark_completed("image", "myrepo:v1.0")
        
        assert state.is_item_processed("image", "myrepo:v1.0")
        assert "image:myrepo:v1.0" in state.completed_items
    
    def test_mark_failed(self, state):
        """Test marking item as failed."""
        state.mark_failed("image", "myrepo:v1.0", "Connection error")
        
        assert state.is_item_processed("image", "myrepo:v1.0")
        assert "image:myrepo:v1.0" in state.failed_items
        assert state.failed_items["image:myrepo:v1.0"] == "Connection error"
    
    def test_mark_skipped(self, state):
        """Test marking item as skipped."""
        state.mark_skipped("chart", "mychart:1.0.0")
        
        assert state.is_item_processed("chart", "mychart:1.0.0")
        assert "chart:mychart:1.0.0" in state.skipped_items
    
    def test_is_complete(self, state):
        """Test completion check."""
        state.set_total_discovered(3)
        assert not state.is_complete
        
        state.mark_completed("image", "a")
        state.mark_failed("image", "b", "error")
        state.mark_skipped("image", "c")
        assert state.is_complete
    
    def test_progress(self, state):
        """Test progress tracking."""
        state.set_total_discovered(10)
        state.mark_completed("image", "a")
        state.mark_completed("image", "b")
        state.mark_failed("image", "c", "err")
        
        processed, total = state.progress
        assert processed == 3
        assert total == 10


class TestMigrationStateBlobCache:
    """Tests for blob caching in MigrationState."""
    
    @pytest.fixture
    def state(self):
        return MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
        )
    
    def test_add_known_blob(self, state):
        """Test adding known blob."""
        state.add_known_blob("sha256:abc123")
        assert state.is_blob_known("sha256:abc123")
    
    def test_unknown_blob(self, state):
        """Test unknown blob check."""
        assert not state.is_blob_known("sha256:unknown")


class TestMigrationStatePersistence:
    """Tests for state persistence."""
    
    @pytest.fixture
    def state_with_file(self, tmp_path):
        return MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path,
        )
    
    @pytest.mark.asyncio
    async def test_save_state(self, state_with_file):
        """Test saving state to file."""
        state_with_file.mark_completed("image", "test:v1")
        state_with_file.add_known_blob("sha256:abc")
        
        await state_with_file.save(force=True)
        
        assert state_with_file._state_file.exists()
        
        # Verify content
        data = json.loads(state_with_file._state_file.read_text())
        assert data["session_id"] == state_with_file.session_id
        assert "image:test:v1" in data["completed_items"]
        assert "sha256:abc" in data["known_blobs"]
    
    @pytest.mark.asyncio
    async def test_load_from_file(self, tmp_path):
        """Test loading state from file."""
        # Create and save state
        state = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path,
        )
        state.mark_completed("image", "test:v1")
        state.set_total_discovered(5)
        await state.save(force=True)
        
        # Load it back
        loaded = await MigrationState._load_from_file(state._state_file)
        
        assert loaded is not None
        assert loaded.session_id == state.session_id
        assert loaded.is_item_processed("image", "test:v1")
        assert loaded.total_discovered == 5
    
    @pytest.mark.asyncio
    async def test_load_or_create_new(self, tmp_path):
        """Test load_or_create creates new when no session exists."""
        state = await MigrationState.load_or_create(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path,
        )
        
        assert state is not None
        assert state.session_id is not None
        assert len(state.completed_items) == 0
    
    @pytest.mark.asyncio
    async def test_load_or_create_resumes(self, tmp_path):
        """Test load_or_create resumes existing session."""
        # Create and save an incomplete session
        original = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path,
        )
        original.set_total_discovered(10)
        original.mark_completed("image", "a")
        original.mark_completed("image", "b")
        await original.save(force=True)
        
        # Load or create should resume
        resumed = await MigrationState.load_or_create(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path,
            resume=True,
        )
        
        assert resumed.session_id == original.session_id
        assert resumed.is_item_processed("image", "a")
        assert resumed.is_item_processed("image", "b")
    
    @pytest.mark.asyncio
    async def test_cleanup_removes_file(self, state_with_file):
        """Test cleanup removes state file."""
        await state_with_file.save(force=True)
        assert state_with_file._state_file.exists()
        
        await state_with_file.cleanup()
        assert not state_with_file._state_file.exists()
