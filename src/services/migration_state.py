"""
Migration State Service (SQLite).

Provides state persistence for migration operations using SQLite3, ensuring
scalability and low memory footprint for large migrations.
"""
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path

from utilities.logger import get_logger

logger = get_logger(__name__)

# Default state directory
DEFAULT_STATE_DIR = ".migrator-state"

class MigrationState:
    """
    Persistent state for a migration session using SQLite.
    
    Tracks completed items, failed items, and blob cache to enable
    resume capability and avoid redundant work.
    """
    
    def __init__(
        self,
        session_id: str,
        db_path: Path,
        source_registry: str,
        dest_registry: str,
        source_namespace: str | None = None,
        dest_namespace: str | None = None,
        start_new: bool = True
    ):
        self.session_id = session_id
        self.db_path = db_path
        self.source_registry = source_registry
        self.dest_registry = dest_registry
        self.source_namespace = source_namespace
        self.dest_namespace = dest_namespace
        
        self._conn = sqlite3.connect(
            str(db_path), 
            check_same_thread=False,
            timeout=30.0
        )
        self._conn.row_factory = sqlite3.Row
        
        if start_new:
            self._init_db()
            self._save_session_info()
            
        self.started_at = self._get_started_at()
        self._total_discovered = self._get_total_discovered()

    def _init_db(self):
        """Initialize database schema."""
        cursor = self._conn.cursor()
        
        # Session info table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session (
                id TEXT PRIMARY KEY,
                started_at TEXT,
                source_registry TEXT,
                dest_registry TEXT,
                source_namespace TEXT,
                dest_namespace TEXT,
                total_discovered INTEGER DEFAULT 0,
                last_checkpoint TEXT
            )
        """)
        
        # Items table (images, charts)
        # Status: 0=discovered, 1=completed, 2=failed, 3=skipped
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS items (
                key TEXT PRIMARY KEY,
                type TEXT,
                reference TEXT,
                status INTEGER,
                error TEXT,
                updated_at REAL
            )
        """)
        
        # Blobs table (repositories where blobs exist)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blobs (
                digest TEXT PRIMARY KEY,
                first_seen REAL
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_items_status ON items(status)")
        self._conn.commit()

    def _save_session_info(self):
        """Save session metadata."""
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO session 
            (id, started_at, source_registry, dest_registry, source_namespace, dest_namespace, total_discovered, last_checkpoint)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.session_id,
            datetime.now().isoformat(),
            self.source_registry,
            self.dest_registry,
            self.source_namespace,
            self.dest_namespace,
            0,
            datetime.now().isoformat()
        ))
        self._conn.commit()

    def _get_started_at(self) -> str:
        cursor = self._conn.cursor()
        cursor.execute("SELECT started_at FROM session WHERE id = ?", (self.session_id,))
        row = cursor.fetchone()
        return row['started_at'] if row else datetime.now().isoformat()

    def _get_total_discovered(self) -> int:
        cursor = self._conn.cursor()
        cursor.execute("SELECT total_discovered FROM session WHERE id = ?", (self.session_id,))
        row = cursor.fetchone()
        return row['total_discovered'] if row else 0

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
        
        if state_dir is None:
            state_dir = Path(DEFAULT_STATE_DIR)
        
        if not state_dir.exists():
            state_dir.mkdir(parents=True, exist_ok=True)
            
        db_path = state_dir / f"{session_id}.db"
        
        logger.info(f"Created new migration session: {session_id}")
        return cls(
            session_id=session_id,
            db_path=db_path,
            source_registry=source_registry,
            dest_registry=dest_registry,
            source_namespace=source_namespace,
            dest_namespace=dest_namespace,
            start_new=True
        )

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
        """Load an existing incomplete session or create a new one."""
        if state_dir is None:
            state_dir = Path(DEFAULT_STATE_DIR)
        
        state_dir = Path(state_dir)
        
        if resume and state_dir.exists():
            # Look for matching incomplete sessions in .db files
            matching = await cls._find_matching_session_db(
                state_dir, source_registry, dest_registry,
                source_namespace, dest_namespace
            )
            if matching:
                logger.info(f"Resuming previous session: {matching.session_id}")
                return matching
        
        return cls.create_new(
            source_registry, dest_registry,
            source_namespace, dest_namespace,
            state_dir
        )

    @classmethod
    async def _find_matching_session_db(
        cls,
        state_dir: Path,
        source_registry: str,
        dest_registry: str,
        source_namespace: str | None,
        dest_namespace: str | None
    ) -> "MigrationState | None":
        """Find a matching incomplete session to resume from SQLite DBs."""
        if not state_dir.exists():
            return None
        
        candidates = []
        
        # Sync check on files is fast enough
        for db_file in state_dir.glob("*.db"):
            try:
                # Check if it matches parameters and is incomplete
                conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                try:
                    cursor.execute("SELECT * FROM session LIMIT 1")
                    row = cursor.fetchone()
                    if not row:
                        continue
                        
                    if (row['source_registry'] == source_registry and
                        row['dest_registry'] == dest_registry and
                        row['source_namespace'] == source_namespace and
                        row['dest_namespace'] == dest_namespace):
                        
                        # Check completeness
                        total = row['total_discovered']
                        cursor.execute("SELECT COUNT(*) as count FROM items WHERE status IN (1, 2, 3)")
                        processed = cursor.fetchone()['count']
                        
                        if total > 0 and processed < total:
                            candidates.append((db_file, db_file.stat().st_mtime))
                finally:
                    conn.close()
                    
            except Exception as e:
                logger.debug(f"Could not check DB file {db_file}: {e}")
                continue
        
        if not candidates:
            return None
        
        # Return most recent matching session
        candidates.sort(key=lambda x: x[1], reverse=True)
        db_file = candidates[0][0]
        session_id = db_file.stem
        
        return cls(
            session_id=session_id,
            db_path=db_file,
            source_registry=source_registry,
            dest_registry=dest_registry,
            source_namespace=source_namespace,
            dest_namespace=dest_namespace,
            start_new=False
        )

    @property
    def total_discovered(self) -> int:
        return self._total_discovered

    @property
    def is_complete(self) -> bool:
        """Check if migration is complete."""
        if self._total_discovered == 0:
            return False
        processed, _ = self.progress
        return processed >= self._total_discovered

    @property
    def progress(self) -> tuple[int, int]:
        """Get (processed, total) counts."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM items WHERE status IN (1, 2, 3)")
        processed = cursor.fetchone()['count']
        return processed, self._total_discovered

    @property
    def completed_items(self) -> set[str]:
        """Get set of completed item keys (for backward compatibility)."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT key FROM items WHERE status = 1")
        return {row['key'] for row in cursor.fetchall()}

    @property
    def known_blobs(self) -> set[str]:
        """Get set of known blobs (for backward compatibility)."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT digest FROM blobs")
        return {row['digest'] for row in cursor.fetchall()}

    def item_key(self, item_type: str, reference: str) -> str:
        return f"{item_type}:{reference}"

    def is_item_processed(self, item_type: str, reference: str) -> bool:
        """Check if an item has already been processed."""
        key = self.item_key(item_type, reference)
        cursor = self._conn.cursor()
        cursor.execute("SELECT 1 FROM items WHERE key = ? AND status IN (1, 2, 3)", (key,))
        return cursor.fetchone() is not None

    def mark_completed(self, item_type: str, reference: str) -> None:
        """Mark an item as successfully completed."""
        key = self.item_key(item_type, reference)
        self._upsert_item(key, item_type, reference, 1, None)

    def mark_failed(self, item_type: str, reference: str, error: str) -> None:
        """Mark an item as failed."""
        key = self.item_key(item_type, reference)
        self._upsert_item(key, item_type, reference, 2, error)

    def mark_skipped(self, item_type: str, reference: str) -> None:
        """Mark an item as skipped."""
        key = self.item_key(item_type, reference)
        self._upsert_item(key, item_type, reference, 3, None)

    def _upsert_item(self, key: str, type_: str, ref: str, status: int, error: str | None):
        with self._conn:
            self._conn.execute("""
                INSERT OR REPLACE INTO items (key, type, reference, status, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (key, type_, ref, status, error, time.time()))

    def add_known_blob(self, digest: str) -> None:
        """Add a blob digest to the known blobs cache."""
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO blobs (digest, first_seen) VALUES (?, ?)", 
                    (digest, time.time())
                )
        except sqlite3.Error as e:
            logger.debug(f"Error adding blob {digest}: {e}")

    def is_blob_known(self, digest: str) -> bool:
        cursor = self._conn.cursor()
        cursor.execute("SELECT 1 FROM blobs WHERE digest = ?", (digest,))
        return cursor.fetchone() is not None

    def set_total_discovered(self, count: int) -> None:
        self._total_discovered = count
        with self._conn:
            self._conn.execute(
                "UPDATE session SET total_discovered = ? WHERE id = ?", 
                (count, self.session_id)
            )

    async def save(self, force: bool = False) -> None:
        """Commit transaction (SQLite does this automatically with context managers above)."""
        pass

    async def cleanup(self) -> None:
        """Clean up state file after successful completion."""
        if self.db_path.exists():
            self._conn.close()
            try:
                self.db_path.unlink()
                logger.debug(f"Cleaned up state DB: {self.db_path}")
            except Exception as e:
                logger.warning(f"Failed to cleanup state DB: {e}")

    @staticmethod
    async def cleanup_old_sessions(
        state_dir: Path = None,
        max_age_days: int = 7
    ) -> int:
        if state_dir is None:
            state_dir = Path(DEFAULT_STATE_DIR)
        
        if not state_dir.exists():
            return 0
            
        removed = 0
        cutoff = time.time() - (max_age_days * 24 * 60 * 60)
        
        # Clean both .json (old) and .db (new) files
        for pattern in ["*.json", "*.db"]:
            for f in state_dir.glob(pattern):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except Exception:
                    pass
                    
        return removed
