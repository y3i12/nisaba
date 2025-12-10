"""Abstract base class for MCP tools."""

import inspect
import logging
import time

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional, TYPE_CHECKING, get_type_hints

try:
    from docstring_parser import parse as parse_docstring
    DOCSTRING_PARSER_AVAILABLE = True
except ImportError:
    DOCSTRING_PARSER_AVAILABLE = False


if TYPE_CHECKING:
    from nisaba.factory import MCPFactory

@dataclass
class BaseToolResponse:
    """Metadata for a nisaba certified return"""
    success:bool = False
    message:Any = None
    nisaba:bool = False

class BaseTool(ABC):
    """
    Abstract base class for all MCP tools.

    Each tool must implement:
    - execute(**kwargs) -> Dict[str, Any]: The main tool logic
    """

    def __init__(self, factory:"MCPFactory"):
        """
        Initialize tool with factory reference.

        Args:
            factory: The MCPFactory that created this tool
        """
        self.factory:"MCPFactory" = factory
        self.config = None
        if factory:
            self.config = factory.config
    
    @classmethod
    def logger(cls):
        return  logging.getLogger(f"{cls.__module__}.{cls.get_name()}")

    @classmethod
    def get_name_from_cls(cls) -> str:
        """
        Get tool name from class name.

        Converts class name like "QueryTool" to "query".

        Returns:
            Tool name in snake_case
        """
        name = cls.__name__
        if name.endswith("Tool"):
            name = name[:-4]
        # Convert to snake_case
        name = "".join(["_" + c.lower() if c.isupper() else c for c in name]).lstrip("_")
        return name

    @classmethod
    def get_name(cls) -> str:
        """Get instance tool name."""
        return cls.get_name_from_cls()

    @classmethod
    @abstractmethod
    def nisaba(cls) -> bool:
        return False
    
    @classmethod
    def get_tool_schema(cls) -> Dict[str, Any]:
        """
        Generate JSON schema from execute() signature and docstring.

        Returns:
            Dict containing tool name, description, and parameter schema
        """
        tool_name = cls.get_name_from_cls()

        # Get execute method
        execute_method = cls.execute
        sig = inspect.signature(execute_method)
        
        # Parse docstring
        docstring_text = execute_method.__doc__ or ""

        if DOCSTRING_PARSER_AVAILABLE and docstring_text:
            docstring = parse_docstring(docstring_text)

            # Build description
            description_parts = []
            if docstring.short_description:
                description_parts.append(docstring.short_description.strip())
            if docstring.long_description:
                description_parts.append(docstring.long_description.strip())

            description = "\n\n".join(description_parts)

            # Build param description map
            param_descriptions = {
                param.arg_name: param.description
                for param in docstring.params
                if param.description
            }
        else:
            description = docstring_text.strip()
            param_descriptions = {}

        # Build parameter schema
        properties = {}
        required = []
        type_hints = get_type_hints(execute_method)

        for param_name, param in sig.parameters.items():
            if param_name in ["self", "kwargs"]:
                continue

            # Get type annotation
            param_type = type_hints.get(param_name, Any)
            json_type = cls._python_type_to_json_type(param_type)

            # Get description from docstring
            param_desc = param_descriptions.get(param_name, "")

            # Build parameter schema entry
            param_schema = {"type": json_type}

            if param_desc:
                param_schema["description"] = param_desc.strip()

            # Add default value if available
            if param.default != inspect.Parameter.empty:
                try:
                    import json
                    json.dumps(param.default)
                    param_schema["default"] = param.default
                except (TypeError, ValueError):
                    pass
            else:
                required.append(param_name)

            properties[param_name] = param_schema

        return {
            "name": tool_name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }

    @classmethod
    def get_tool_description(cls) -> str:
        """
        Get human-readable tool description.

        Returns:
            Description string extracted from docstrings
        """
        execute_doc = cls.execute.__doc__ or ""

        if DOCSTRING_PARSER_AVAILABLE and execute_doc:
            docstring = parse_docstring(execute_doc)
            return docstring.short_description or cls.__doc__ or ""

        if execute_doc:
            return execute_doc.strip().split('\n')[0]
        return cls.__doc__ or ""
    
    @abstractmethod
    async def execute(self, **kwargs) -> BaseToolResponse:
        """
        Execute the tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            BaseToolResponse
        """
        pass

    async def execute_tool(self, **kwargs) -> BaseToolResponse:
        """
        Execute tool with automatic timing and error handling.

        Args:
            **kwargs: Tool-specific parameters

        Returns:
            Tool execution result with timing
        """
        try:
            result = await self.execute(**kwargs)

            return result

        except Exception as e:
            return self.response_exception(e, "Tool execution exception")

    @classmethod
    def is_optional(cls) -> bool:
        """
        Check if tool is optional (disabled by default).

        Returns:
            True if tool is optional
        """
        from ..markers import ToolMarkerOptional
        return issubclass(cls, ToolMarkerOptional)

    @classmethod
    def is_dev_only(cls) -> bool:
        """
        Check if tool is development-only.

        Returns:
            True if tool is dev-only
        """
        from ..markers import ToolMarkerDevOnly
        return issubclass(cls, ToolMarkerDevOnly)

    @classmethod
    def is_mutating(cls) -> bool:
        """
        Check if tool modifies state.

        Returns:
            True if tool mutates state
        """
        from ..markers import ToolMarkerMutating
        return issubclass(cls, ToolMarkerMutating)

    # UTILITY METHODS
    @classmethod
    def _python_type_to_json_type(cls, python_type: Any) -> str:
        """
        Convert Python type hint to JSON schema type.

        Args:
            python_type: Python type annotation

        Returns:
            JSON schema type string
        """
        # Handle string representations
        if isinstance(python_type, str):
            type_str = python_type.lower()
            if 'str' in type_str:
                return "string"
            elif 'int' in type_str:
                return "integer"
            elif 'float' in type_str or 'number' in type_str:
                return "number"
            elif 'bool' in type_str:
                return "boolean"
            elif 'list' in type_str or 'sequence' in type_str:
                return "array"
            elif 'dict' in type_str:
                return "object"
            return "string"

        # Get the origin for generic types
        origin = getattr(python_type, '__origin__', None)

        # Handle None/NoneType
        if python_type is type(None):
            return "null"

        # Direct type mappings
        type_map = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        if python_type in type_map:
            return type_map[python_type]

        # Handle Optional, Union, List, Dict, etc.
        if origin is not None:
            if origin in (list, tuple):
                return "array"
            elif origin is dict:
                return "object"
            elif hasattr(python_type, '__args__'):
                # For Union types, try first non-None type
                for arg in python_type.__args__:
                    if arg is not type(None):
                        return cls._python_type_to_json_type(arg)

        # Default to string for unknown types
        return "string"
    
    # CONVENIANCE TOOL RETURN METHODS
    @classmethod
    def response(cls, success:bool = False, message:Any = None) -> BaseToolResponse:
        """Return response."""
        return BaseToolResponse(success=success, message=message, nisaba=cls.nisaba())
    
    @classmethod
    def response_success(cls, message:Any = None) -> BaseToolResponse:
        """Return error response."""
        return cls.response(success=True, message=message)
    
    @classmethod
    def response_error(cls, message:Any = None, exc_info:bool=False) -> BaseToolResponse:
        """Return error response."""
        cls.logger().error(message, exc_info=exc_info)
        return cls.response(success=False, message=message)
    
    @classmethod
    def response_exception(cls, e:Exception, message:Any = None) -> BaseToolResponse:
        """Return exception response."""
        if message is None:
            return cls.response_error(message=f"Exception - {type(e).__name__}: {str(e)}", exc_info=True)
        else:
            return cls.response_error(message=f"{message} - {type(e).__name__}: {str(e)}", exc_info=True)

