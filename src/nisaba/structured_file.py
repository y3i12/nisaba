"""
Structured file management with caching, locking, and derived data.

Provides base classes for thread-safe and process-safe file operations
with intelligent caching and automatic computation of derived data
(token counts, section counts, etc.).
"""

import fcntl
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class StructuredFileCache:
    """
    Base class for file caching with automatic derived data computation.
    
    Features:
    - mtime-based cache validation (no redundant reads)
    - Write updates cache from memory (no invalidation/re-read)
    - Thread-safe and process-safe with fcntl locking
    - Automatic computation of derived data (tokens, sections, lines)
    - Tag wrapping for system prompt injection
    """
    
    def __init__(
        self,
        file_path: Path,
        name: str,
        tag: Optional[str] = None,
        section_marker: Optional[str] = None
    ):
        """
        Initialize structured file cache.
        
        Args:
            file_path: Path to the file
            name: Human-readable name for logging
            tag: Optional tag for wrapping content (e.g., "---FLOATING_WINDOWS")
            section_marker: Optional marker for counting sections (e.g., "---WIDGET_")
        """
        self.file_path = Path(file_path)
        self.name = name
        self.tag = tag
        self.section_marker = section_marker
        
        # Cache state
        self.content: str = ""
        self._last_mtime: Optional[float] = None
        
        # Derived data cache
        self._cached_token_count: int = 0
        self._cached_section_count: int = 0
        self._cached_line_count: int = 0
        
        # Thread safety (in-process coordination)
        self._lock = threading.RLock()
    
    def load(self) -> str:
        """
        Load content with mtime-based cache validation.
        
        Returns cached content if file hasn't changed, otherwise re-reads.
        Wraps content with tags if configured.
        
        Returns:
            Content wrapped with tags (if tag is set)
        """
        with self._lock:
            if not self.file_path.exists():
                return self._wrap_with_tags("")
            
            # Check if file changed
            current_mtime = self.file_path.stat().st_mtime
            
            if current_mtime != self._last_mtime:
                # File changed externally → re-read and recalculate
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock for read
                    try:
                        self.content = f.read()
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                
                self._last_mtime = current_mtime
                self._update_derived_data()
                logger.debug(f"[{self.name}] Loaded from disk (mtime changed)")
            else:
                logger.debug(f"[{self.name}] Using cached content")
            
            return self._wrap_with_tags(self.content)
    
    def write(self, content: str) -> None:
        """
        Write content and update cache from memory (no re-read!).
        
        This is the key optimization: after writing, we keep the content
        in memory and update derived data from it, rather than invalidating
        the cache and forcing a re-read.
        
        Args:
            content: Content to write
        """
        with self._lock:
            # Ensure directory exists
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Atomic write with exclusive lock
            with open(self.file_path, 'w', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock
                try:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            
            # Update cache from memory (content we just wrote)
            self.content = content
            self._last_mtime = self.file_path.stat().st_mtime
            self._update_derived_data()  # Recalculate from in-memory content
            
            logger.debug(f"[{self.name}] Written to disk and cache updated")
    
    def atomic_update(self, modifier_fn: Callable[[str], str]) -> None:
        """
        Atomic read-modify-write transaction.
        
        Holds exclusive lock for the entire transaction to prevent races.
        
        Args:
            modifier_fn: Function that takes current content and returns new content
        """
        with self._lock:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create file if doesn't exist
            if not self.file_path.exists():
                self.file_path.write_text("")
            
            # Hold exclusive lock for entire read-modify-write transaction
            with open(self.file_path, 'r+', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    # Read current state
                    f.seek(0)
                    current_content = f.read()
                    
                    # Modify
                    new_content = modifier_fn(current_content)
                    
                    # Write back
                    f.seek(0)
                    f.truncate()
                    f.write(new_content)
                    f.flush()
                    os.fsync(f.fileno())
                    
                    # Update cache from memory
                    self.content = new_content
                    self._last_mtime = self.file_path.stat().st_mtime
                    self._update_derived_data()
                    
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            
            logger.debug(f"[{self.name}] Atomic update completed")
    
    def _update_derived_data(self) -> None:
        """Recalculate all derived data from current content."""
        self._cached_token_count = self._estimate_tokens(self.content)
        self._cached_line_count = self.content.count('\n')
        
        if self.section_marker:
            self._cached_section_count = int(self.content.count(self.section_marker) / 2)
    
    def _estimate_tokens(self, content: str) -> int:
        """
        Estimate token count using simple heuristic.
        
        Override in subclasses for more accurate estimation.
        """
        return len(content) // 4
    
    def _wrap_with_tags(self, content: str) -> str:
        """Wrap content with tags for system prompt injection."""
        if not self.tag:
            return content
        return f"---{self.tag}\n{content}\n---{self.tag}_END"
    
    # Properties for derived data (cached, no recomputation)
    
    @property
    def token_count(self) -> int:
        """Get cached token count."""
        return self._cached_token_count
    
    @property
    def section_count(self) -> int:
        """Get cached section count."""
        return self._cached_section_count
    
    @property
    def line_count(self) -> int:
        """Get cached line count."""
        return self._cached_line_count
    
    @property
    def is_empty(self) -> bool:
        """Check if content is empty."""
        return len(self.content) == 0


class JsonStructuredFile(StructuredFileCache):
    """
    Specialized structured file for JSON files.
    
    Provides JSON-specific operations with the same caching and locking
    guarantees as the base class.
    
    Use cases:
    - mcp_servers.json (registry)
    - State files (augments, file_windows, etc.)
    """
    
    def __init__(
        self,
        file_path: Path,
        name: str,
        default_factory: Optional[Callable[[], dict]] = None
    ):
        """
        Initialize JSON structured file.
        
        Args:
            file_path: Path to JSON file
            name: Human-readable name
            default_factory: Optional factory for default JSON structure
        """
        super().__init__(file_path, name, tag=None, section_marker=None)
        self.default_factory = default_factory or dict
        self._cached_data: dict = {}
    
    def load_json(self) -> dict:
        """
        Load and parse JSON content.
        
        Returns cached parsed data if file hasn't changed.
        
        Returns:
            Parsed JSON data
        """
        with self._lock:
            if not self.file_path.exists():
                return self.default_factory()
            
            # Check if file changed
            current_mtime = self.file_path.stat().st_mtime
            
            if current_mtime != self._last_mtime:
                # File changed → re-read and parse
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        self.content = f.read()
                        try:
                            self._cached_data = json.loads(self.content)
                        except json.JSONDecodeError as e:
                            logger.warning(f"[{self.name}] Invalid JSON, using default: {e}")
                            self._cached_data = self.default_factory()
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                
                self._last_mtime = current_mtime
                self._update_derived_data()
                logger.debug(f"[{self.name}] Loaded JSON from disk")
            
            return self._cached_data
    
    def write_json(self, data: dict, indent: int = 2) -> None:
        """
        Write JSON data and update cache from memory.
        
        Args:
            data: Dictionary to serialize as JSON
            indent: JSON indentation (default: 2)
        """
        with self._lock:
            # Serialize to JSON
            content = json.dumps(data, indent=indent)
            
            # Write using base class method (handles locking)
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.file_path, 'w', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            
            # Update cache from memory
            self.content = content
            self._cached_data = data
            self._last_mtime = self.file_path.stat().st_mtime
            self._update_derived_data()
            
            logger.debug(f"[{self.name}] Written JSON to disk and cache updated")
    
    def atomic_update_json(self, modifier_fn: Callable[[dict], dict]) -> None:
        """
        Atomic read-modify-write transaction for JSON data.
        
        Args:
            modifier_fn: Function that takes current data dict and returns new data dict
        """
        with self._lock:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create with default if doesn't exist
            if not self.file_path.exists():
                self.write_json(self.default_factory())
            
            # Hold exclusive lock for entire transaction
            with open(self.file_path, 'r+', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    # Read and parse
                    f.seek(0)
                    try:
                        current_data = json.load(f)
                    except json.JSONDecodeError:
                        current_data = self.default_factory()
                    
                    # Modify
                    new_data = modifier_fn(current_data)
                    
                    # Serialize and write
                    content = json.dumps(new_data, indent=2)
                    f.seek(0)
                    f.truncate()
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                    
                    # Update cache from memory
                    self.content = content
                    self._cached_data = new_data
                    self._last_mtime = self.file_path.stat().st_mtime
                    self._update_derived_data()
                    
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            
            logger.debug(f"[{self.name}] Atomic JSON update completed")
