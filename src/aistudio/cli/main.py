"""Root ``aistudio`` command group and global options (spec §6.1)."""

from __future__ import annotations

import logging

import click

from aistudio import __version__
from aistudio.cli import profile_cmd, session_cmd
from aistudio.cli.state import CliState


@click.group()
@click.version_option(__version__, prog_name="aistudio")
@click.option("-p", "--profile", "profile_name",
              help="Profile name (default: $AISTUDIO_PROFILE, config, or 'default').")
@click.option("--backend", type=click.Choice(["auto", "http", "browser"]), default="auto",
              show_default=True, help="Force a transport (debugging).")
@click.option("-v", "--verbose", count=True, help="-v for INFO, -vv for DEBUG.")
@click.option("--quiet", is_flag=True, help="Only print errors.")
@click.pass_context
def cli(ctx: click.Context, profile_name: str | None, backend: str, verbose: int,
        quiet: bool) -> None:
    """Unofficial CLI for the Google AI Studio web app."""
    if verbose and quiet:
        raise click.UsageError("--quiet cannot be combined with -v")
    level = logging.ERROR if quiet else {0: logging.WARNING, 1: logging.INFO}.get(
        verbose, logging.DEBUG
    )
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    ctx.obj = CliState(profile_name=profile_name, backend=backend, verbose=verbose, quiet=quiet)


cli.add_command(session_cmd.auth)
cli.add_command(session_cmd.login)
cli.add_command(profile_cmd.profile)


def main() -> None:
    cli()
