"""
Claude CLI wrapper command for nisaba.

Provides a click command that wraps the real claude CLI with augments
injection via mitmproxy.
"""

import json
import os
import socket
import sys
import shutil
from pathlib import Path

import click


def _find_free_port() -> int:
    """Ask the OS for a free port. Tiny TOCTOU race is acceptable for local dev."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


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
        default=None,
        help="Port for mitmproxy (default: auto-allocate)"
    )
    @click.option(
        "--mcp-port",
        type=int,
        default=None,
        help="Port for nisaba MCP HTTP server (default: auto-allocate)"
    )
    @click.option(
        "--debug-proxy",
        is_flag=True,
        help="Show mitmproxy debug output"
    )
    def claude_wrapper(
        claude_args: tuple,
        proxy_port: int,
        mcp_port: int,
        debug_proxy: bool,
    ):
        """
        Run Claude CLI with augments injection proxy.

        Starts mitmproxy and the nisaba MCP server on auto-allocated ports
        (or the explicit --proxy-port / --mcp-port if given), then launches
        the real claude CLI with HTTPS_PROXY pointing at the proxy and the
        nisaba MCP server injected via --mcp-config.

        This means multiple `nisaba claude` instances can run in parallel
        without port collisions, and `.mcp.json` does not need a nisaba entry.

        Examples:

            \b
            # Default: auto-allocate both ports
            nisaba claude

            \b
            # Pass arguments through to claude
            nisaba claude --continue

            \b
            # Pin ports (useful for attaching mitmweb / debugging)
            nisaba claude --proxy-port 1337 --mcp-port 9973

            \b
            # Debug proxy (show intercepts)
            nisaba claude --debug-proxy
        """
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

        # 3. Resolve ports (auto-allocate if not pinned)
        if proxy_port is None:
            proxy_port = _find_free_port()
        if mcp_port is None:
            mcp_port = _find_free_port()

        click.echo(f"🚀 Starting Nisaba — proxy:{proxy_port} mcp:{mcp_port}", err=True)

        # 4. Build inline MCP config so claude discovers nisaba on the resolved port
        nisaba_mcp_config = {
            "mcpServers": {
                "nisaba": {
                    "type": "http",
                    "url": f"http://localhost:{mcp_port}/mcp",
                }
            }
        }
        modified_claude_args = [
            "--mcp-config", json.dumps(nisaba_mcp_config),
            *claude_args,
        ]

        # 5. Start unified server (proxy + MCP)
        import asyncio
        from nisaba.compact import clear_active_pointer, write_active_pointer
        from nisaba.wrapper.unified import UnifiedNisabaServer

        instance_id = str(os.getpid())
        write_active_pointer(instance_id)

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
                env["NISABA_INSTANCE_ID"] = instance_id

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

                click.echo(f"🤖 Executing: {real_claude} {' '.join(claude_args)}\n", err=True)

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
                clear_active_pointer(instance_id)

                return result.returncode

            except KeyboardInterrupt:
                click.echo("\n\n⚠️  Interrupted by user", err=True)
                await server.stop()
                clear_active_pointer(instance_id)
                return 130
            except Exception as e:
                clear_active_pointer(instance_id)
                raise e

        # Run the async workflow
        try:
            returncode = asyncio.run(run_with_claude())
            sys.exit(returncode)
        except KeyboardInterrupt:
            click.echo("\n\n⚠️  Interrupted by user", err=True)
            sys.exit(130)

    return claude_wrapper
