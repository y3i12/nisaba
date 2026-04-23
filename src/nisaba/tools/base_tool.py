"""Abstract base class for MCP tools."""

import inspect
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, get_type_hints

try:
    from docstring_parser import parse as parse_docstring
    DOCSTRING_PARSER_AVAILABLE = True
except ImportError:
    DOCSTRING_PARSER_AVAILABLE = False


@dataclass
class BaseToolResponse:
    success: bool = False
    message: Any = None


class BaseTool(ABC):
    """Abstract base class for MCP tools."""

    @classmethod
    def logger(cls):
        return logging.getLogger(f"{cls.__module__}.{cls.get_name()}")

    @classmethod
    def get_name_from_cls(cls) -> str:
        """Convert `FooBarTool` → `foo_bar`."""
        name = cls.__name__
        if name.endswith("Tool"):
            name = name[:-4]
        name = "".join(["_" + c.lower() if c.isupper() else c for c in name]).lstrip("_")
        return name

    @classmethod
    def get_name(cls) -> str:
        return cls.get_name_from_cls()

    @classmethod
    def get_tool_schema(cls) -> Dict[str, Any]:
        """Generate JSON schema from execute() signature and docstring."""
        tool_name = cls.get_name_from_cls()
        execute_method = cls.execute
        sig = inspect.signature(execute_method)
        docstring_text = execute_method.__doc__ or ""

        if DOCSTRING_PARSER_AVAILABLE and docstring_text:
            docstring = parse_docstring(docstring_text)
            description_parts = []
            if docstring.short_description:
                description_parts.append(docstring.short_description.strip())
            if docstring.long_description:
                description_parts.append(docstring.long_description.strip())
            description = "\n\n".join(description_parts)
            param_descriptions = {
                param.arg_name: param.description
                for param in docstring.params
                if param.description
            }
        else:
            description = docstring_text.strip()
            param_descriptions = {}

        properties: Dict[str, Any] = {}
        required: list[str] = []
        type_hints = get_type_hints(execute_method)

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "kwargs"):
                continue
            param_type = type_hints.get(param_name, Any)
            json_type = cls._python_type_to_json_type(param_type)
            param_schema: Dict[str, Any] = {"type": json_type}
            param_desc = param_descriptions.get(param_name, "")
            if param_desc:
                param_schema["description"] = param_desc.strip()
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
                "required": required,
            },
        }

    @abstractmethod
    async def execute(self, **kwargs) -> BaseToolResponse:
        """Execute the tool with given parameters."""
        pass

    async def execute_tool(self, **kwargs) -> BaseToolResponse:
        try:
            return await self.execute(**kwargs)
        except Exception as e:
            return self.response_exception(e, "Tool execution exception")

    @classmethod
    def _python_type_to_json_type(cls, python_type: Any) -> str:
        if isinstance(python_type, str):
            type_str = python_type.lower()
            if "str" in type_str:
                return "string"
            if "int" in type_str:
                return "integer"
            if "float" in type_str or "number" in type_str:
                return "number"
            if "bool" in type_str:
                return "boolean"
            if "list" in type_str or "sequence" in type_str:
                return "array"
            if "dict" in type_str:
                return "object"
            return "string"

        if python_type is type(None):
            return "null"

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

        origin = getattr(python_type, "__origin__", None)
        if origin is not None:
            if origin in (list, tuple):
                return "array"
            if origin is dict:
                return "object"
            if hasattr(python_type, "__args__"):
                for arg in python_type.__args__:
                    if arg is not type(None):
                        return cls._python_type_to_json_type(arg)
        return "string"

    @classmethod
    def response(cls, success: bool = False, message: Any = None) -> BaseToolResponse:
        return BaseToolResponse(success=success, message=message)

    @classmethod
    def response_success(cls, message: Any = None) -> BaseToolResponse:
        return cls.response(success=True, message=message)

    @classmethod
    def response_error(cls, message: Any = None, exc_info: bool = False) -> BaseToolResponse:
        cls.logger().error(message, exc_info=exc_info)
        return cls.response(success=False, message=message)

    @classmethod
    def response_exception(cls, e: Exception, message: Any = None) -> BaseToolResponse:
        if message is None:
            return cls.response_error(
                message=f"Exception - {type(e).__name__}: {str(e)}", exc_info=True
            )
        return cls.response_error(
            message=f"{message} - {type(e).__name__}: {str(e)}", exc_info=True
        )
