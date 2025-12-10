"""Template engine for MCP instruction generation."""

import re
import logging
from pathlib import Path
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class InstructionsTemplateEngine:
    """
    Generic template engine for MCP instruction generation.

    Supports:
    - Loading templates from file or string
    - Placeholder replacement ({{PLACEHOLDER_NAME}})
    - Conditional file inclusion (Read(@path[:condition]))
    - Two-pass rendering (Read directives → placeholders → cleanup)
    - Dynamic content injection
    """

    def __init__(
        self,
        template_path: Optional[Path] = None,
        template_string: Optional[str] = None,
        runtime_context: Optional[Dict] = None
    ):
        """
        Initialize template engine.

        Args:
            template_path: Path to template file
            template_string: Template as string (alternative to file)
            runtime_context: Runtime context for conditional includes (e.g., {'dev_mode': True})

        Raises:
            ValueError: If neither template_path nor template_string provided
        """
        self.placeholders: Dict[str, str] = {}
        self.template: Optional[str] = None
        self.template_path = template_path  # Store for relative path resolution
        self.runtime_context = runtime_context or {}

        if template_string:
            self.template = template_string
        elif template_path:
            self._load_template(template_path)
        else:
            raise ValueError("Must provide either template_path or template_string")


    def _load_template(self, template_path: Path) -> None:
        """
        Load template from file.

        Args:
            template_path: Path to template file
        """
        try:
            if not template_path.exists():
                logger.warning(f"Template file not found: {template_path}")
                self.template = ""
                return

            with open(template_path, 'r', encoding='utf-8') as f:
                self.template = f.read()

            logger.debug(f"Loaded template from {template_path} ({len(self.template)} chars)")

        except Exception as e:
            logger.error(f"Failed to load template from {template_path}: {e}", exc_info=True)
            self.template = ""

    def _expand_read_directives(self, text: str) -> str:
        """
        Expand Read(@path[:condition]) directives.

        Examples:
            Read(@./background.md)              -> always include
            Read(@./debug_info.md:dev-mode)     -> only if dev_mode=True
            Read(@./advanced.md:!dev-mode)      -> only if dev_mode=False

        Args:
            text: Text to process

        Returns:
            Text with Read directives replaced by file contents
        """
        pattern = r'Read\(@([^:)]+)(?::([^)]+))?\)'

        def replace_with_content(match):
            path = match.group(1).strip()
            condition = match.group(2)  # e.g., "dev-mode" or None

            # Evaluate condition
            if condition:
                should_include = self._eval_condition(condition)
                if not should_include:
                    logger.debug(f"Skipping {path} (condition '{condition}' not met)")
                    return ""  # Skip this inclusion

            # Include the file
            try:
                file_path = Path(path)
                if not file_path.is_absolute():
                    # Resolve relative to template location
                    if self.template_path:
                        file_path = self.template_path.parent / path

                content = file_path.read_text(encoding='utf-8')
                logger.debug(f"Included {path} ({len(content)} chars)")
                return content

            except Exception as e:
                logger.warning(f"Failed to read {path}: {e}")
                return f"<!-- Error reading {path}: {e} -->"

        return re.sub(pattern, replace_with_content, text)

    def _eval_condition(self, condition: str) -> bool:
        """
        Evaluate a condition string against runtime context.

        Supports:
            - "dev-mode" -> runtime_context.get('dev_mode')
            - "!dev-mode" -> not runtime_context.get('dev_mode')

        Args:
            condition: Condition string to evaluate

        Returns:
            True if condition is met
        """
        negate = condition.startswith('!')
        if negate:
            condition = condition[1:]

        # Map condition names to context keys
        condition_map = {
            'dev-mode': 'dev_mode',
            # Could add more: 'production', 'verbose', etc.
        }

        context_key = condition_map.get(condition)
        if context_key is None:
            logger.warning(f"Unknown condition: {condition}")
            return False

        result = self.runtime_context.get(context_key, False)
        return not result if negate else result

    def add_placeholder_content(self, name: str, content: str) -> None:
        """
        Register content for a placeholder.

        Args:
            name: Placeholder name (without {{ }})
            content: Content to replace placeholder with
        """
        self.placeholders[name] = content

    def render(self, **placeholders: str) -> str:
        """
        Render template with placeholders.

        Args:
            **placeholders: Key-value pairs for {{KEY}} replacement

        Returns:
            Rendered instructions string
        """
        if not self.template:
            logger.warning("No template loaded")
            return ""

        # Merge provided placeholders with stored ones
        all_placeholders = {**self.placeholders, **placeholders}

        # Start with template
        result = self.template

        # Replace placeholders
        for key, value in all_placeholders.items():
            placeholder = f"{{{{{key}}}}}"
            result = result.replace(placeholder, value)

        # Log any unused placeholders (optional, for debugging)
        unused = self._find_unused_placeholders(result)
        if unused:
            logger.debug(f"Unused placeholders in template: {', '.join(unused)}")

        return result

    def clear_unused_placeholders(self, text: Optional[str] = None) -> str:
        """
        Remove any {{PLACEHOLDER}} that wasn't replaced.

        Args:
            text: Text to clear placeholders from (uses rendered template if None)

        Returns:
            Text with unused placeholders removed
        """
        if text is None:
            if not self.template:
                return ""
            text = self.template

        # Remove all remaining {{PLACEHOLDER}} patterns
        result = re.sub(r'\{\{[A-Z_]+\}\}', '', text)

        # Clean up extra blank lines
        result = re.sub(r'\n\n\n+', '\n\n', result)

        return result

    def _find_unused_placeholders(self, text: str) -> list:
        """
        Find all unused placeholders in text.

        Args:
            text: Text to search

        Returns:
            List of unused placeholder names
        """
        matches = re.findall(r'\{\{([A-Z_]+)\}\}', text)
        return matches

    def render_and_clear(self, **placeholders: str) -> str:
        """
        Two-pass rendering with Read directive expansion.

        Pass 1: Expand Read(@...) directives
        Pass 2: Replace {{PLACEHOLDERS}}
        Pass 3: Clear unused placeholders

        Args:
            **placeholders: Key-value pairs for {{KEY}} replacement

        Returns:
            Fully rendered instructions
        """
        if not self.template:
            logger.warning("No template loaded")
            return ""

        # PASS 1: Expand Read directives
        # Included files can themselves contain {{PLACEHOLDERS}}
        expanded_text = self._expand_read_directives(self.template)
        logger.debug(f"After Read expansion: {len(expanded_text)} chars")

        # PASS 2: Replace placeholders in combined text
        # Merge provided placeholders with stored ones
        all_placeholders = {**self.placeholders, **placeholders}

        for key, value in all_placeholders.items():
            placeholder = f"{{{{{key}}}}}"
            expanded_text = expanded_text.replace(placeholder, value)

        logger.debug(f"After placeholder replacement: {len(expanded_text)} chars")

        # PASS 3: Clear unused placeholders
        result = self.clear_unused_placeholders(expanded_text)
        logger.debug(f"After cleanup: {len(result)} chars")

        return result
