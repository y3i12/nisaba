"""
Nisaba CLI - Minimal command-line interface for Claude Code proxy.
"""

import click
from nisaba.wrapper import create_claude_wrapper_command


@click.group()
@click.version_option()
def cli():
    """Nisaba - Augments management and proxy for Claude Code."""
    pass


# Register claude wrapper command
cli.add_command(create_claude_wrapper_command())


if __name__ == "__main__":
    cli()
