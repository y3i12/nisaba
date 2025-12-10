"""
Claude CLI wrapper command for nisaba.

Provides a click command that wraps the real claude CLI with augments
injection via mitmproxy.
"""

import os
import sys
import shutil
import subprocess
import time
from pathlib import Path

import click


def create_claude_wrapper_command():
    """
    Create a click command that wraps claude CLI with augments injection.

    This factory function returns a click.Command that can be added to
    any MCP's CLI via cli.add_command().

    Returns:
        click.Command for claude wrapper
    """

    @click.command(
        "claude",
        context_settings=dict(
            ignore_unknown_options=True,
            allow_interspersed_args=False
        )
    )
    @click.argument("claude_args", nargs=-1, type=click.UNPROCESSED)
    @click.option(
        "--proxy-port",
        type=int,
        default=1337,
        help="Port for mitmproxy (default: 1337)"
    )
    @click.option(
        "--debug-proxy",
        is_flag=True,
        help="Show mitmproxy debug output"
    )
    @click.option(
        "--list-servers",
        is_flag=True,
        help="List available MCP servers and exit"
    )
    def claude_wrapper(
        claude_args: tuple,
        proxy_port: int,
        debug_proxy: bool,
        list_servers: bool
    ):
        """
        Run Claude CLI with augments injection proxy.

        This command starts mitmproxy in the background to intercept
        Anthropic API requests and inject augments content by replacing
        __NISABA_AUGMENTS_PLACEHOLDER__ in the system prompt.

        The proxy runs on port 1337 (or --proxy-port) and is automatically
        stopped when claude exits.

        Examples:

            \b
            # Run claude with default augments (./test.md)
            nabu claude

            \b
            # Pass arguments to claude
            nabu claude --project myproject
            nabu claude -m "analyze this code"

            \b
            # Debug proxy (show intercepts)
            nabu claude --debug-proxy
        """
        # Handle --list-servers
        if list_servers:
            from nisaba.mcp_registry import MCPServerRegistry

            registry_path = Path.cwd() / ".nisaba" / "mcp_servers.json"

            if not registry_path.exists():
                click.echo("No MCP servers registered.", err=True)
                sys.exit(0)

            try:
                registry = MCPServerRegistry(registry_path)
                servers = registry.list_servers()

                if not servers:
                    click.echo("No active MCP servers found.", err=True)
                    sys.exit(0)

                click.echo(f"📡 Available MCP Servers ({len(servers)}):\n", err=True)

                for server_id, info in servers.items():
                    click.echo(f"  • {info['name']} (PID: {info['pid']})", err=True)
                    if info.get('http', {}).get('enabled'):
                        click.echo(f"    HTTP: {info['http']['url']}", err=True)
                    click.echo(f"    Started: {info['started_at']}", err=True)
                    click.echo(f"    CWD: {info['cwd']}\n", err=True)

                sys.exit(0)

            except Exception as e:
                click.echo(f"❌ Error reading registry: {e}", err=True)
                sys.exit(1)

        # 1. Find real claude binary
        real_claude = shutil.which("claude")
        if not real_claude:
            click.echo("❌ Error: claude command not found in PATH", err=True)
            click.echo("\nMake sure Claude CLI is installed and in your PATH.", err=True)
            sys.exit(1)

        # 2. Verify augments directory exists
        augments_dir = Path.cwd() / ".nisaba" / "augments"
        if not augments_dir.exists():
            click.echo(f"⚠️  Warning: Augments directory not found: {augments_dir}", err=True)
            click.echo("Augments system will start with no augments loaded.\n", err=True)

        # Build modified claude_args with system prompt injection
        modified_claude_args = list(claude_args)

        # being injected by the proxy
        # Add --append-system-prompt before other args
        # modified_claude_args = ["--debug"] + modified_claude_args

        # 4. Start unified server (proxy + MCP)
        click.echo(f"🚀 Starting unified Nisaba server...", err=True)

        import asyncio
        from nisaba.wrapper.unified import UnifiedNisabaServer

        # MCP port (9973 - last prime before 10000)
        mcp_port = 9973

        # Create unified server
        server = UnifiedNisabaServer(
            augments_dir=augments_dir,
            proxy_port=proxy_port,
            mcp_port=mcp_port,
            debug_proxy=debug_proxy
        )

        async def run_with_claude():
            """Run unified server and execute claude CLI."""
            try:
                # Start unified server
                await server.start()

                # Setup environment for claude CLI
                env = os.environ.copy()
                env["HTTPS_PROXY"] = f"http://localhost:{proxy_port}"
                env["HTTP_PROXY"] = f"http://localhost:{proxy_port}"

                # SSL certificate setup for mitmproxy
                mitmproxy_ca = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
                if mitmproxy_ca.exists():
                    env["SSL_CERT_FILE"] = str(mitmproxy_ca)
                    env["REQUESTS_CA_BUNDLE"] = str(mitmproxy_ca)
                    env["NODE_EXTRA_CA_CERTS"] = str(mitmproxy_ca)
                    env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
                    click.echo(f"🔒 Using mitmproxy CA: {mitmproxy_ca}", err=True)
                else:
                    click.echo("⚠️  Warning: mitmproxy CA certificate not found", err=True)
                    click.echo(f"   Expected at: {mitmproxy_ca}", err=True)
                    click.echo("   Run mitmproxy once to generate certificates", err=True)

                click.echo(f"🤖 Executing: {real_claude} {' '.join(modified_claude_args)}\n", err=True)

                # Run claude CLI as subprocess (blocking)
                result = await asyncio.create_subprocess_exec(
                    real_claude,
                    *modified_claude_args,
                    env=env,
                    stdin=sys.stdin,
                    stdout=sys.stdout,
                    stderr=sys.stderr
                )

                # Wait for claude to finish
                await result.wait()

                # Cleanup
                await server.stop()

                return result.returncode

            except KeyboardInterrupt:
                click.echo("\n\n⚠️  Interrupted by user", err=True)
                await server.stop()
                return 130
            except Exception as e:
                raise e
                click.echo(f"\n❌ Error: {e}", err=True)
                await server.stop()
                return 1

        # Run the async workflow
        try:
            returncode = asyncio.run(run_with_claude())
            sys.exit(returncode)
        except KeyboardInterrupt:
            click.echo("\n\n⚠️  Interrupted by user", err=True)
            sys.exit(130)

    return claude_wrapper
