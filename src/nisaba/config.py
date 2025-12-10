"""MCP server configuration."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Set, Optional

from nisaba.utils import load_yaml


@dataclass
class MCPContext:
    """
    Context configuration for MCP server.

    Defines which tools are enabled/disabled and provides
    tool-specific configuration overrides.
    """

    name: str = "default"
    description: str = ""
    enabled_tools: Set[str] = field(default_factory=set)
    disabled_tools: Set[str] = field(default_factory=set)
    tool_config_overrides: dict = field(default_factory=dict)
    openai_compatible: bool = False  # Apply OpenAI schema sanitization

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "MCPContext":
        """
        Load context from YAML file.

        Args:
            yaml_path: Path to YAML configuration file

        Returns:
            Loaded MCPContext instance
        """
        data = load_yaml(yaml_path, preserve_comments=False)

        name = data.pop("name", yaml_path.stem)
        description = data.pop("description", "")
        enabled_tools = set(data.pop("enabled_tools", []))
        disabled_tools = set(data.pop("disabled_tools", []))
        tool_config_overrides = data.pop("tool_config_overrides", {})
        openai_compatible = data.pop("openai_compatible", False)

        # Store remaining config for subclass extension
        context = cls(
            name=name,
            description=description,
            enabled_tools=enabled_tools,
            disabled_tools=disabled_tools,
            tool_config_overrides=tool_config_overrides,
            openai_compatible=openai_compatible
        )

        # Attach extra config as attribute for subclass access
        context._extra_config = data

        return context

    @classmethod
    def load(
        cls,
        context_name_or_path: str,
        contexts_dir: Optional[Path] = None
    ) -> "MCPContext":
        """
        Load context by name or path.

        Args:
            context_name_or_path: Context name or path to YAML file
            contexts_dir: Directory containing context files (default: auto-detect)

        Returns:
            Loaded MCPContext instance
        """
        # If path provided, load directly
        if context_name_or_path.endswith(".yml") or context_name_or_path.endswith(".yaml"):
            return cls.from_yaml(Path(context_name_or_path))

        # Otherwise look in contexts directory
        if contexts_dir is None:
            # Default to "contexts" subdirectory relative to this file
            contexts_dir = Path(__file__).parent / "contexts"

        context_file = contexts_dir / f"{context_name_or_path}.yml"

        if not context_file.exists():
            raise FileNotFoundError(
                f"Context '{context_name_or_path}' not found at {context_file}"
            )

        return cls.from_yaml(context_file)

    @classmethod
    def load_default(cls, contexts_dir: Optional[Path] = None) -> "MCPContext":
        """
        Load the default context.

        Args:
            contexts_dir: Directory containing context files

        Returns:
            Default MCPContext instance
        """
        return cls.load("default", contexts_dir=contexts_dir)


@dataclass
class MCPConfig:
    """
    Base MCP server configuration.

    Subclasses should extend this with project-specific settings.
    """

    # Core settings
    server_name: str = "mcp_server"
    dev_mode: bool = False
    log_level: str = "INFO"

    # Tool settings
    # Note: Subclasses SHOULD override this with their own context type and default
    # Example: context: NabuContext = field(default_factory=lambda: NabuContext.load_default(...))
    context: MCPContext = field(default_factory=MCPContext)
    tool_timeout: float = 120.0  # seconds

    # Dual transport settings
    enable_http_transport: bool = False
    http_host: str = "0.0.0.0"
    http_port: int = 9973  # Last prime before 10000
    http_log_to_stderr: bool = True  # Prevent stdout pollution

    # Optional config file tracking (for persistent configs)
    config_file_path: Optional[Path] = None

    @classmethod
    def from_yaml(cls, yaml_path: Path, **overrides) -> "MCPConfig":
        """
        Load config from YAML file.

        Subclasses can override for custom loading logic (e.g., commented YAML preservation).

        Args:
            yaml_path: Path to YAML configuration file
            **overrides: Override specific fields after loading

        Returns:
            Config instance with values from YAML
        """
        data = load_yaml(yaml_path, preserve_comments=False)

        # Extract known fields with defaults from class
        config_dict = {
            "server_name": data.get("server_name", cls.server_name),
            "dev_mode": data.get("dev_mode", cls.dev_mode),
            "log_level": data.get("log_level", cls.log_level),
            "tool_timeout": data.get("tool_timeout", cls.tool_timeout),
            "config_file_path": yaml_path,
        }

        # Load context if specified
        if "context" in data:
            context_name = data["context"]
            config_dict["context"] = MCPContext.load(context_name)

        # Apply overrides (take precedence over YAML)
        config_dict.update(overrides)

        # Create instance
        instance = cls(**config_dict)

        # Store extra config for subclass extension
        instance._extra_config = data

        return instance
