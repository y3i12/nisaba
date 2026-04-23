"""Build a FastMCP server with the augment tool registered."""

import inspect
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP
from typing_extensions import Annotated
from pydantic import Field

from nisaba.tools.augment import AugmentTool
from nisaba.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

_JSON_TO_PY = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


@asynccontextmanager
async def _lifespan(mcp_server: FastMCP) -> AsyncIterator[None]:
    logger.info("Nisaba MCP Server - Ready")
    yield
    logger.info("Nisaba MCP Server - Shutdown")


def _register_tool(mcp: FastMCP, tool: BaseTool) -> None:
    """
    Register a BaseTool with FastMCP via a dynamically-built typed wrapper.

    FastMCP introspects real Python annotations to generate the parameter
    schema shown to LLM clients, so we build a function with type hints that
    match the tool's declared schema and hand it to `mcp.tool(...)`.
    """
    schema = tool.get_tool_schema()
    tool_name = tool.get_name()
    description = schema.get("description", "")
    params_schema = schema.get("parameters", {})
    properties = params_schema.get("properties", {})
    required = set(params_schema.get("required", []))

    annotations: dict = {}
    param_defs: list[str] = []
    for param_name, param_info in properties.items():
        python_type = _JSON_TO_PY.get(param_info.get("type", "string"), str)
        param_desc = param_info.get("description", "")
        default_value = param_info.get("default", inspect.Parameter.empty)

        annotations[param_name] = (
            Annotated[python_type, Field(description=param_desc)]
            if param_desc else python_type
        )

        if param_name in required:
            param_defs.append(param_name)
        elif default_value == inspect.Parameter.empty:
            param_defs.append(f"{param_name}=None")
        else:
            param_defs.append(f"{param_name}={default_value!r}")

    param_list = ", ".join(param_defs)
    kwargs_build = "{" + ", ".join(f"'{p}': {p}" for p in properties) + "}"

    func_code = f"""
async def typed_wrapper({param_list}):
    from dataclasses import asdict
    kwargs = {kwargs_build}
    response = await tool_instance.execute_tool(**kwargs)
    return asdict(response) if not isinstance(response, dict) else response
"""
    namespace = {"tool_instance": tool}
    exec(func_code, namespace)
    wrapper = namespace["typed_wrapper"]
    wrapper.__annotations__ = annotations

    mcp.tool(name=tool_name, description=description)(wrapper)


def create_nisaba_server(host: str = "0.0.0.0", port: int = 9973) -> FastMCP:
    """Build the nisaba FastMCP server with AugmentTool registered."""
    mcp = FastMCP(
        name="nisaba",
        lifespan=_lifespan,
        host=host,
        port=port,
        instructions="",
    )
    _register_tool(mcp, AugmentTool())
    return mcp
