"""
Todo management tool for nisaba workspace.
"""
from typing import Any, Dict, List, TYPE_CHECKING
from pathlib import Path
from nisaba.tools.base_operation_tool import BaseOperationTool, Operation
from nisaba.workspace_files import WorkspaceFiles
from nisaba import session_context

if TYPE_CHECKING:
    from nisaba.factory import MCPFactory

class TodoTool(BaseOperationTool):
    """Todo list operations in workspace

    The todo list is contained in the workspace in the message section, as the last message, wrapped in
    `<system_reminder></system_reminder>`
    """

    def __init__(self, factory: "MCPFactory"):
        """Initialize the TodoWriteTool with StructuredFileCache."""
        super().__init__(factory)
        # Use StructuredFileCache for atomic read-modify-write operations
    
    @classmethod
    def get_todo_file_cache(cls):
        """Get session-specific todos cache from WorkspaceFiles."""
        session_id = session_context.get_current_session()
        if not session_id:
            raise RuntimeError("No active session - cannot access todo list")
        return WorkspaceFiles.instance(session_id).todos

    @classmethod
    def nisaba(cls) -> bool:
        return True

    @classmethod
    def get_operation_config(cls) -> Dict[str,Operation]:
        return cls.make_operations([
                cls.make_operation(
                    command=cls.add,
                    name='add',
                    description='Add todo(s) to the list, optionally at position',
                    result_formatter=cls._format_str,
                    parameters=[
                        cls.make_parameter(name='todos',    required=True,  type='array',   description='list of todos, eg: ["todo1", "todo2"]'),
                        cls.make_parameter(name='position', required=False, type='integer', description='position to insert the todos (defaults to last)'),
                    ]
                ),
                cls.make_operation(
                    command=cls.remove,
                    name='remove',
                    description='Remove todo(s) from the list by index or indices',
                    result_formatter=cls._format_str,
                    parameters=[
                        cls.make_parameter(name='index',   required_or='indices', type='integer', description='Todo item index'),
                        cls.make_parameter(name='indices', required=True,         type='array',   description='List of todo item indices'),
                    ]
                ),
                cls.make_operation(
                    command=cls.mark_done,
                    name='mark_done',
                    description='Marks todo as done by index or indices',
                    result_formatter=cls._format_str,
                    parameters=[
                        cls.make_parameter(name='index',   required_or='indices', type='integer', description='Todo item index'),
                        cls.make_parameter(name='indices', required=True,         type='array',   description='List of todo item indices'),
                    ]
                ),
                cls.make_operation(
                    command=cls.clear,
                    name='clear',
                    description='Clears the todo list',
                    result_formatter=cls._format_ok,
                    parameters=[],
                    skip_render=True
                )
            ])
    
    @classmethod
    def _parse_todos(cls, content: str) -> List[Dict[str, Any]]:
        """
        Parse numbered markdown list into structured format.
        
        Args:
            content: File content with numbered todos
            
        Returns:
            List of dicts with 'content' and 'done' keys
        """
        import re
        pattern = r'^\s*\d+\.\s*\[([ x])\]\s*(.+)$'
        
        todos = []
        for line in content.strip().split('\n'):
            if not line.strip():
                continue
            match = re.match(pattern, line)
            if match:
                done = match.group(1) == 'x'
                content_text = match.group(2).strip()
                todos.append({
                    "content": content_text,
                    "done": done
                })
        return todos
    
    @classmethod
    def _format_todos(cls, todos: List[Dict[str, Any]]) -> str:
        """
        Format structured todos into numbered markdown.
        
        Args:
            todos: List of dicts with 'content' and 'done' keys
            
        Returns:
            Formatted markdown string
        """
        lines = []
        for i, todo in enumerate(todos, start=1):
            checkbox = 'x' if todo.get('done', False) else ' '
            content = todo.get('content', '')
            lines.append(f"{i}. [{checkbox}] {content}")
        return '\n'.join(lines)
    
    @classmethod
    def _validate_indices(cls, indices: List[int], max_index: int) -> None:
        """
        Validate indices are within bounds (1-based).
        
        Args:
            indices: List of indices to validate
            max_index: Maximum valid index
            
        Raises:
            ValueError: If any index is out of bounds
        """
        for idx in indices:
            if idx < 1 or idx > max_index:
                raise ValueError(f"Index {idx} out of bounds (valid: 1-{max_index})")
        
    @classmethod
    def clear(cls) -> bool:
        cls.get_todo_file_cache().write("")
        return True
    
    @classmethod
    def add(cls, todos:List[str], position:int|None = None) -> str:
        # Use list to capture actual position in closure
        actual_position = [position]

        new_todos = []
        for item in todos:
            new_todos.append({
                "content": item,
                "done": False
            })

        def extend_todos(content: str) -> str:
            """Extend todos at position."""
            existing = cls._parse_todos(content) if content.strip() else []
            
            # Determine insert position
            pos = position
            if pos is None:
                pos = len(existing) + 1
            else:
                if pos < 1:
                    pos = 1
                if pos > len(existing) + 1:
                    pos = len(existing) + 1
            
            actual_position[0] = pos
            
            # Insert at position (1-based to 0-based)
            insert_idx = pos - 1
            result = existing[:insert_idx] + new_todos + existing[insert_idx:]
            
            return cls._format_todos(result)
                
        # Atomic update
        cls.get_todo_file_cache().atomic_update(extend_todos)
        
        return f"Added {len(new_todos)} items at pos {actual_position[0]}"
            
    
    @classmethod
    def remove(cls, index:int|None = None, indices:List[int]|None = None) -> str:
        target_indices:List[int] = []
        if index is not None:
            target_indices.append(index)
        else:
            assert(indices)
            target_indices = indices    
        
        # Use list to capture removed items in closure
        removed_items = []
        
        def remove_items(content: str) -> str:
            """Remove todos by index."""
            existing = cls._parse_todos(content) if content.strip() else []
            cls._validate_indices(target_indices, len(existing))
            
            # Collect items to remove for notification
            for idx in target_indices:
                removed_items.append({
                    "index": idx,
                    "content": existing[idx - 1]["content"]
                })
            
            # Remove in descending order to preserve indices
            for idx in sorted(target_indices, reverse=True):
                del existing[idx - 1]  # Convert 1-based to 0-based
            
            return cls._format_todos(existing)
        
        # Atomic update
        cls.get_todo_file_cache().atomic_update(remove_items)
        
        return f"Removed {len(removed_items)} items"
        
    
    @classmethod
    def mark_done(cls, index:int|None = None, indices:List[int]|None = None) -> str:
        target_indices:List[int] = []
        if index is not None:
            target_indices.append(index)
        else:
            assert(indices)
            target_indices = indices

        # Use list to capture marked items in closure
        marked_items = []
        
        def mark_done(content: str) -> str:
            """Mark todos as done."""
            existing = cls._parse_todos(content) if content.strip() else []
            cls._validate_indices(target_indices, len(existing))
            
            # Collect items for notification
            for idx in target_indices:
                marked_items.append({
                    "index": idx,
                    "content": existing[idx - 1]["content"]
                })
            
            # Mark as done
            for idx in target_indices:
                existing[idx - 1]["done"] = True
            
            return cls._format_todos(existing)
        
        # Atomic update
        cls.get_todo_file_cache().atomic_update(mark_done)
        
        return f"Marked {len(marked_items)} items as done"

    def _render(self):
        pass