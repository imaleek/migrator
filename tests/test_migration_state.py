"""
Unit tests for the Migration State Service (SQLite).

Tests state persistence, resume capability, and session management using SQLite.
"""
import asyncio
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import time

from services.migration_state import MigrationState, DEFAULT_STATE_DIR


class TestMigrationStateCreation:
    """Tests for MigrationState creation."""
    
    def test_create_new_state(self, tmp_path):
        """Test creating a new migration state."""
        state = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            source_namespace="project-a",
            dest_namespace="project-b",
            state_dir=tmp_path
        )
        
        assert state.session_id is not None
        assert state.source_registry == "source.io"
        assert state.dest_registry == "dest.io"
        assert state.source_namespace == "project-a"
        assert state.dest_namespace == "project-b"
        assert len(state.completed_items) == 0
        assert len(state.known_blobs) == 0
        
        # Verify DB file exists
        assert state.db_path.exists()
        assert state.db_path.suffix == ".db"
        
        # Close connection to clean up
        state._conn.close()


class TestMigrationStateItems:
    """Tests for item tracking in MigrationState."""
    
    @pytest.fixture
    def state(self, tmp_path):
        s = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path
        )
        yield s
        # Cleanup
        try:
            s._conn.close()
        except:
            pass
    
    def test_item_key_generation(self, state):
        """Test item key generation."""
        key = state.item_key("image", "myrepo:v1.0")
        assert key == "image:myrepo:v1.0"
    
    def test_mark_completed(self, state):
        """Test marking item as completed."""
        state.mark_completed("image", "myrepo:v1.0")
        
        assert state.is_item_processed("image", "myrepo:v1.0")
        assert "image:myrepo:v1.0" in state.completed_items
        
        # Verify directly in DB
        cursor = state._conn.cursor()
        cursor.execute("SELECT status FROM items WHERE key = ?", ("image:myrepo:v1.0",))
        assert cursor.fetchone()['status'] == 1
    
    def test_mark_failed(self, state):
        """Test marking item as failed."""
        state.mark_failed("image", "myrepo:v1.0", "Connection error")
        
        assert state.is_item_processed("image", "myrepo:v1.0")
        # Failed items are processed but not in completed_items set
        assert "image:myrepo:v1.0" not in state.completed_items
        
        # Verify directly in DB
        cursor = state._conn.cursor()
        cursor.execute("SELECT status, error FROM items WHERE key = ?", ("image:myrepo:v1.0",))
        row = cursor.fetchone()
        assert row['status'] == 2
        assert row['error'] == "Connection error"
    
    def test_mark_skipped(self, state):
        """Test marking item as skipped."""
        state.mark_skipped("chart", "mychart:1.0.0")
        
        assert state.is_item_processed("chart", "mychart:1.0.0")
        assert "chart:mychart:1.0.0" not in state.completed_items
        
        # Verify directly in DB
        cursor = state._conn.cursor()
        cursor.execute("SELECT status FROM items WHERE key = ?", ("chart:mychart:1.0.0",))
        assert cursor.fetchone()['status'] == 3
    
    def test_is_complete(self, state):
        """Test completion check."""
        state.set_total_discovered(3)
        assert not state.is_complete
        
        state.mark_completed("image", "a")
        state.mark_failed("image", "b", "error")
        state.mark_skipped("image", "c")
        
        assert state.is_complete
        
        # Add another item
        state.set_total_discovered(4)
        assert not state.is_complete
    
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
    def state(self, tmp_path):
        s = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path
        )
        yield s
        s._conn.close()
    
    def test_add_known_blob(self, state):
        """Test adding known blob."""
        state.add_known_blob("sha256:abc123")
        assert state.is_blob_known("sha256:abc123")
        assert "sha256:abc123" in state.known_blobs
    
    def test_unknown_blob(self, state):
        """Test unknown blob check."""
        assert not state.is_blob_known("sha256:unknown")


class TestMigrationStatePersistence:
    """Tests for state persistence/resume."""
    
    @pytest.mark.asyncio
    async def test_resume_session(self, tmp_path):
        """Test loading/resuming a session."""
        # Create initial state
        state1 = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path
        )
        session_id = state1.session_id
        
        state1.set_total_discovered(10)
        state1.mark_completed("image", "item1")
        state1.add_known_blob("blob1")
        state1._conn.close()
        
        # Delay to ensure timestamp diff if needed (though not strictly needed for ID matching)
        
        # Resume
        state2 = await MigrationState.load_or_create(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path,
            resume=True
        )
        
        assert state2.session_id == session_id
        assert state2.total_discovered == 10
        assert state2.is_item_processed("image", "item1")
        assert state2.is_blob_known("blob1")
        
        state2._conn.close()

    @pytest.mark.asyncio
    async def test_no_resume_creates_new(self, tmp_path):
        """Test that disabled resume creates new session."""
        state1 = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path
        )
        state1.set_total_discovered(10)
        state1._conn.close()
        
        # Create new WITHOUT resume
        state2 = await MigrationState.load_or_create(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path,
            resume=False
        )
        
        assert state2.session_id != state1.session_id
        state2._conn.close()

    @pytest.mark.asyncio
    async def test_cleanup(self, tmp_path):
        """Test cleanup removes DB file."""
        state = MigrationState.create_new(
            source_registry="source.io",
            dest_registry="dest.io",
            state_dir=tmp_path
        )
        db_path = state.db_path
        assert db_path.exists()
        
        await state.cleanup()
        assert not db_path.exists()
