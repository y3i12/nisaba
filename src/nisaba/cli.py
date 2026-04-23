"""
Nisaba CLI - Minimal command-line interface for Claude Code proxy.
"""

import sys

import click
from nisaba.wrapper import create_claude_wrapper_command


@click.group()
@click.version_option()
def cli():
    """Nisaba - Augments management and proxy for Claude Code."""
    pass


cli.add_command(create_claude_wrapper_command())


@cli.command()
@click.option(
    "--instance",
    "instance_id",
    default=None,
    help="Explicit nisaba instance id (PID) to target. Defaults to $NISABA_INSTANCE_ID.",
)
@click.option(
    "--session",
    "session_id",
    default=None,
    help="Explicit session UUID to compact (bypasses instance lookup).",
)
def compact(instance_id, session_id):
    """
    Extract the active session's transcript and append it to compacted_transcript.md.

    Discovery order: --session, then --instance or $NISABA_INSTANCE_ID,
    then (if exactly one nisaba instance is active) auto-detect.
    """
    from nisaba.compact import (
        CompactError,
        TRANSCRIPT_CACHE,
        append_to_cache,
        extract_transcript,
        resolve_session,
    )

    try:
        resolved_session, jsonl_path = resolve_session(
            explicit_instance=instance_id,
            explicit_session=session_id,
        )
    except CompactError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)

    click.echo(f"📜 Extracting session {resolved_session}", err=True)
    transcript = extract_transcript(jsonl_path, resolved_session)
    if not transcript:
        click.echo("⚠️  No transcript content extracted", err=True)
        sys.exit(0)

    append_to_cache(transcript)
    click.echo(f"✅ Appended to {TRANSCRIPT_CACHE}", err=True)


if __name__ == "__main__":
    cli()
