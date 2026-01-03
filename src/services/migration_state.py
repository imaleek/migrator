"""
Migration State Service.

Provides state persistence for migration operations, enabling:
- Resume capability after process interruption
- Checkpoint saving during migration
- Session management for concurrent migrations
"""
import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from utilities.logger import get_logger

logger = get_logger(__name__)

# Default state directory
DEFAULT_STATE_DIR = ".migrator-state"


@dataclass
class MigrationState:
    """
    Persistent state for a migration session.
    
    Tracks completed items, failed items, and blob cache to enable
    resume capability and avoid redundant work.
    """
    session_id: str
    started_at: str  # ISO format timestamp
    source_registry: str
    dest_registry: str
    source_namespace: str | None = None
    dest_namespace: str | None = None
    total_discovered: int = 0
    completed_items: set[str] = field(default_factory=set)
    failed_items: dict[str, str] = field(default_factory=dict)  # key -> error
    skipped_items: set[str] = field(default_factory=set)
    known_blobs: set[str] = field(default_factory=set)  # Blobs known to exist in dest
    last_checkpoint: str | None = None  # ISO format
    
    # Runtime fields (not persisted)
    _state_file: Path | None = field(default=None, repr=False)
    _dirty: bool = field(default=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    
    @classmethod
    def create_new(
        cls,
        source_registry: str,
        dest_registry: str,
        source_namespace: str | None = None,
        dest_namespace: str | None = None,
        state_dir: Path | None = None
    ) -> "MigrationState":
        """Create a new migration state session."""
        session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        
        state = cls(
            session_id=session_id,
            started_at=datetime.now().isoformat(),
            source_registry=source_registry,
            dest_registry=dest_registry,
            source_namespace=source_namespace,
            dest_namespace=dest_namespace,
        )
        
        if state_dir:
            state._state_file = state_dir / f"{session_id}.json"
        
        logger.info(f"Created new migration session: {session_id}")
        return state
    
    @classmethod
    async def load_or_create(
        cls,
        source_registry: str,
        dest_registry: str,
        source_namespace: str | None = None,
        dest_namespace: str | None = None,
        state_dir: Path = None,
        resume: bool = True
    ) -> "MigrationState":
        """
        Load an existing incomplete session or create a new one.
        
        Args:
            source_registry: Source registry host
            dest_registry: Destination registry host
            source_namespace: Source namespace/prefix
            dest_namespace: Destination namespace/prefix
            state_dir: Directory for state files
            resume: Whether to attempt resuming previous session
        
        Returns:
            MigrationState instance (loaded or new)
        """
        if state_dir is None:
            state_dir = Path(DEFAULT_STATE_DIR)
        
        state_dir = Path(state_dir)
        
        if resume and state_dir.exists():
            # Look for matching incomplete sessions
            matching = await cls._find_matching_session(
                state_dir, source_registry, dest_registry,
                source_namespace, dest_namespace
            )
            if matching:
                logger.info(f"Resuming previous session: {matching.session_id}")
                return matching
        
        # Create new session
        state_dir.mkdir(parents=True, exist_ok=True)
        state = cls.create_new(
            source_registry, dest_registry,
            source_namespace, dest_namespace,
            state_dir
        )
        return state
    
    @classmethod
    async def _find_matching_session(
        cls,
        state_dir: Path,
        source_registry: str,
        dest_registry: str,
        source_namespace: str | None,
        dest_namespace: str | None
    ) -> "MigrationState | None":
        """Find a matching incomplete session to resume."""
        if not state_dir.exists():
            return None
        
        candidates = []
        
        for state_file in state_dir.glob("*.json"):
            try:
                state = await cls._load_from_file(state_file)
                if state is None:
                    continue
                
                # Check if this session matches and is incomplete
                if (state.source_registry == source_registry and
                    state.dest_registry == dest_registry and
                    state.source_namespace == source_namespace and
                    state.dest_namespace == dest_namespace and
                    not state.is_complete):
                    candidates.append((state, state_file.stat().st_mtime))
            except Exception as e:
                logger.debug(f"Could not load state file {state_file}: {e}")
                continue
        
        if not candidates:
            return None
        
        # Return most recent matching session
        candidates.sort(key=lambda x: x[1], reverse=True)
        state, _ = candidates[0]
        return state
    
    @classmethod
    async def _load_from_file(cls, state_file: Path) -> "MigrationState | None":
        """Load state from a JSON file."""
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            
            state = cls(
                session_id=data["session_id"],
                started_at=data["started_at"],
                source_registry=data["source_registry"],
                dest_registry=data["dest_registry"],
                source_namespace=data.get("source_namespace"),
                dest_namespace=data.get("dest_namespace"),
                total_discovered=data.get("total_discovered", 0),
                completed_items=set(data.get("completed_items", [])),
                failed_items=data.get("failed_items", {}),
                skipped_items=set(data.get("skipped_items", [])),
                known_blobs=set(data.get("known_blobs", [])),
                last_checkpoint=data.get("last_checkpoint"),
            )
            state._state_file = state_file
            return state
        except Exception as e:
            logger.warning(f"Failed to load state from {state_file}: {e}")
            return None
    
    @property
    def is_complete(self) -> bool:
        """Check if migration is complete (all discovered items processed)."""
        if self.total_discovered == 0:
            return False
        processed = len(self.completed_items) + len(self.failed_items) + len(self.skipped_items)
        return processed >= self.total_discovered
    
    @property
    def progress(self) -> tuple[int, int]:
        """Get (processed, total) counts."""
        processed = len(self.completed_items) + len(self.failed_items) + len(self.skipped_items)
        return processed, self.total_discovered
    
    def item_key(self, item_type: str, reference: str) -> str:
        """Generate a unique key for an item."""
        return f"{item_type}:{reference}"
    
    def is_item_processed(self, item_type: str, reference: str) -> bool:
        """Check if an item has already been processed."""
        key = self.item_key(item_type, reference)
        return key in self.completed_items or key in self.failed_items or key in self.skipped_items
    
    def mark_completed(self, item_type: str, reference: str) -> None:
        """Mark an item as successfully completed."""
        key = self.item_key(item_type, reference)
        self.completed_items.add(key)
        self.failed_items.pop(key, None)  # Remove from failed if was there
        self._dirty = True
    
    def mark_failed(self, item_type: str, reference: str, error: str) -> None:
        """Mark an item as failed."""
        key = self.item_key(item_type, reference)
        self.failed_items[key] = error
        self._dirty = True
    
    def mark_skipped(self, item_type: str, reference: str) -> None:
        """Mark an item as skipped (already existed)."""
        key = self.item_key(item_type, reference)
        self.skipped_items.add(key)
        self._dirty = True
    
    def add_known_blob(self, digest: str) -> None:
        """Add a blob digest to the known blobs cache."""
        self.known_blobs.add(digest)
        self._dirty = True
    
    def is_blob_known(self, digest: str) -> bool:
        """Check if a blob is known to exist in destination."""
        return digest in self.known_blobs
    
    def set_total_discovered(self, count: int) -> None:
        """Set the total number of discovered items."""
        self.total_discovered = count
        self._dirty = True
    
    async def save(self, force: bool = False) -> None:
        """
        Save state to disk.
        
        Args:
            force: Save even if not dirty
        """
        if not self._state_file:
            return
        
        if not force and not self._dirty:
            return
        
        async with self._lock:
            try:
                data = {
                    "session_id": self.session_id,
                    "started_at": self.started_at,
                    "source_registry": self.source_registry,
                    "dest_registry": self.dest_registry,
                    "source_namespace": self.source_namespace,
                    "dest_namespace": self.dest_namespace,
                    "total_discovered": self.total_discovered,
                    "completed_items": list(self.completed_items),
                    "failed_items": self.failed_items,
                    "skipped_items": list(self.skipped_items),
                    "known_blobs": list(self.known_blobs),
                    "last_checkpoint": datetime.now().isoformat(),
                }
                
                # Write atomically using temp file
                temp_file = self._state_file.with_suffix(".tmp")
                temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                temp_file.replace(self._state_file)
                
                self._dirty = False
                self.last_checkpoint = data["last_checkpoint"]
                logger.debug(f"Saved migration state checkpoint: {self.session_id}")
                
            except Exception as e:
                logger.error(f"Failed to save migration state: {e}")
    
    async def cleanup(self) -> None:
        """Clean up state file after successful completion."""
        if self._state_file and self._state_file.exists():
            try:
                self._state_file.unlink()
                logger.debug(f"Cleaned up state file: {self._state_file}")
            except Exception as e:
                logger.warning(f"Failed to cleanup state file: {e}")
    
    @staticmethod
    async def cleanup_old_sessions(
        state_dir: Path = None,
        max_age_days: int = 7
    ) -> int:
        """
        Clean up old state files.
        
        Args:
            state_dir: Directory containing state files
            max_age_days: Remove sessions older than this
        
        Returns:
            Number of files removed
        """
        if state_dir is None:
            state_dir = Path(DEFAULT_STATE_DIR)
        
        if not state_dir.exists():
            return 0
        
        removed = 0
        cutoff = time.time() - (max_age_days * 24 * 60 * 60)
        
        for state_file in state_dir.glob("*.json"):
            try:
                if state_file.stat().st_mtime < cutoff:
                    state_file.unlink()
                    removed += 1
                    logger.debug(f"Removed old state file: {state_file.name}")
            except Exception as e:
                logger.debug(f"Failed to remove old state file {state_file}: {e}")
        
        if removed > 0:
            logger.info(f"Cleaned up {removed} old migration state files")
        
        return removed
