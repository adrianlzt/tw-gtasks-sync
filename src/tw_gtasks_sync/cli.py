"""Command-line interface for tw-gtasks-sync."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from tw_gtasks_sync import __version__
from tw_gtasks_sync.config import (
    AccountConfig,
    configure_uda,
    get_data_dir,
    init_config,
    is_uda_configured,
    load_config,
    save_config,
)
from tw_gtasks_sync.gtasks_side import GTasksSide
from tw_gtasks_sync.notify import notify_error, notify_sync_complete
from tw_gtasks_sync.sync import Synchronizer
from tw_gtasks_sync.tw_side import TaskWarriorSide

console = Console()


@click.group()
@click.version_option(version=__version__)
@click.option("-v", "--verbose", count=True, help="Increase verbosity")
@click.pass_context
def main(ctx: click.Context, verbose: int) -> None:
    """Synchronize Google Tasks with Taskwarrior.

    Bidirectional sync between Google Tasks lists and Taskwarrior tags.
    Supports multiple Google accounts with different sync configurations.
    """
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@main.command()
@click.option(
    "--account",
    "-a",
    "account_name",
    help="Sync a specific account (syncs all accounts if not specified)",
)
@click.option(
    "--oauth-port",
    default=8081,
    help="Port for OAuth callback server",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force update all tasks (ignore cache)",
)
@click.pass_context
def sync(ctx: click.Context, account_name: str | None, oauth_port: int, force: bool) -> None:
    """Synchronize tasks between Google Tasks and Taskwarrior.

    If no account is specified, syncs all configured accounts.
    """
    config = load_config()

    if not config.accounts:
        console.print("[yellow]No accounts configured.[/yellow]")
        console.print("Run 'tw-gtasks-sync auth' to add an account first.")
        sys.exit(1)

    accounts_to_sync = []
    if account_name:
        if account_name not in config.accounts:
            console.print(f"[red]Account '{account_name}' not found.[/red]")
            console.print("Available accounts:")
            for name in config.accounts:
                console.print(f"  - {name}")
            sys.exit(1)
        accounts_to_sync = [config.accounts[account_name]]
    else:
        accounts_to_sync = list(config.accounts.values())

    if not is_uda_configured():
        console.print("[yellow]UDA not configured in Taskwarrior.[/yellow]")
        console.print("Running 'tw-gtasks-sync init' first...")
        configure_uda()
        console.print("[green]UDA configured successfully.[/green]")

    total_stats = {
        "created_tw": 0,
        "created_gtasks": 0,
        "updated_tw": 0,
        "updated_gtasks": 0,
        "deleted_tw": 0,
        "deleted_gtasks": 0,
        "conflicts": 0,
    }

    for account in accounts_to_sync:
        console.print(f"\n[cyan]Syncing account: {account.name}[/cyan]")
        console.print(f"  Google Tasks list: {account.google_list}")
        console.print(f"  Taskwarrior tag: +{account.tw_tag}")

        try:
            stats = _sync_account(
                account, oauth_port, verbose=ctx.obj.get("verbose", 0), force=force
            )

            total_stats["created_tw"] += stats.created_tw
            total_stats["created_gtasks"] += stats.created_gtasks
            total_stats["updated_tw"] += stats.updated_tw
            total_stats["updated_gtasks"] += stats.updated_gtasks
            total_stats["deleted_tw"] += stats.deleted_tw
            total_stats["deleted_gtasks"] += stats.deleted_gtasks
            total_stats["conflicts"] += stats.conflicts

            if ctx.obj.get("verbose", 0) > 0:
                console.print(f"  [green]Created in TW: {stats.created_tw}[/green]")
                console.print(f"  [green]Created in GTasks: {stats.created_gtasks}[/green]")
                console.print(f"  [blue]Updated in TW: {stats.updated_tw}[/blue]")
                console.print(f"  [blue]Updated in GTasks: {stats.updated_gtasks}[/blue]")
                console.print(f"  [yellow]Deleted in TW: {stats.deleted_tw}[/yellow]")
                console.print(f"  [yellow]Deleted in GTasks: {stats.deleted_gtasks}[/yellow]")
                if stats.conflicts:
                    console.print(f"  [red]Conflicts: {stats.conflicts}[/red]")

            notify_sync_complete(
                account_name=account.name,
                created_tw=stats.created_tw,
                created_gtasks=stats.created_gtasks,
                updated_tw=stats.updated_tw,
                updated_gtasks=stats.updated_gtasks,
                deleted_tw=stats.deleted_tw,
                deleted_gtasks=stats.deleted_gtasks,
                conflicts=stats.conflicts,
            )

        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")
            notify_error(str(e), account.name)
            if ctx.obj.get("verbose", 0) > 0:
                import traceback

                traceback.print_exc()

    if len(accounts_to_sync) > 1:
        console.print("\n[bold]Total:[/bold]")
        console.print(f"  Created in TW: {total_stats['created_tw']}")
        console.print(f"  Created in GTasks: {total_stats['created_gtasks']}")
        console.print(f"  Updated in TW: {total_stats['updated_tw']}")
        console.print(f"  Updated in GTasks: {total_stats['updated_gtasks']}")
        console.print(f"  Deleted in TW: {total_stats['deleted_tw']}")
        console.print(f"  Deleted in GTasks: {total_stats['deleted_gtasks']}")
        if total_stats["conflicts"]:
            console.print(f"  [red]Conflicts: {total_stats['conflicts']}[/red]")


def _sync_account(
    account: AccountConfig, oauth_port: int, verbose: int = 0, force: bool = False
):
    """Sync a single account."""
    gtasks = GTasksSide(
        credentials_file=account.credentials_file,
        oauth_port=oauth_port,
        task_list_name=account.google_list,
    )

    tw = TaskWarriorSide(tag=account.tw_tag, exclude_uda=account.exclude_uda)

    gtasks.start()
    tw.start()

    serdes_dir = get_data_dir() / "serdes" / account.name

    synchronizer = Synchronizer(
        gtasks_side=gtasks,
        tw_side=tw,
        account=account,
        serdes_dir=serdes_dir,
        force=force,
    )

    stats = synchronizer.sync()

    gtasks.finish()
    tw.finish()

    return stats


@main.command()
@click.option(
    "--account",
    "-a",
    "account_name",
    required=True,
    help="Account name for this sync configuration",
)
@click.option(
    "--google-list",
    "-l",
    required=True,
    help="Name of the Google Tasks list to sync",
)
@click.option(
    "--tw-tag",
    "-t",
    required=True,
    help="Taskwarrior tag to sync (without the +)",
)
@click.option(
    "--oauth-port",
    default=8081,
    help="Port for OAuth callback server",
)
@click.option(
    "--exclude-uda",
    default=None,
    help="Exclude tasks that have this UDA set (e.g., 'jiraid')",
)
@click.pass_context
def auth(
    ctx: click.Context,
    account_name: str,
    google_list: str,
    tw_tag: str,
    oauth_port: int,
    exclude_uda: str | None,
) -> None:
    """Authenticate and configure a new sync account.

    This will open a browser for Google OAuth authentication.
    """
    config = load_config()

    if account_name in config.accounts:
        console.print(f"[yellow]Account '{account_name}' already exists.[/yellow]")
        if not click.confirm("Do you want to update it?"):
            console.print("Cancelled.")
            return

    console.print(f"\n[cyan]Setting up account: {account_name}[/cyan]")
    console.print(f"  Google Tasks list: {google_list}")
    console.print(f"  Taskwarrior tag: +{tw_tag}")
    console.print(f"  OAuth port: {oauth_port}")
    if exclude_uda:
        console.print(f"  Exclude UDA: {exclude_uda}")
    console.print()

    console.print("[yellow]A browser window will open for Google authentication.[/yellow]")
    console.print("Please grant access to Google Tasks when prompted.")
    console.print()

    credentials_file = f"credentials_{account_name}.pickle"

    gtasks = GTasksSide(
        credentials_file=credentials_file,
        oauth_port=oauth_port,
        task_list_name=google_list,
    )

    try:
        gtasks.authenticate()
        console.print("[green]✓ Authentication successful![/green]")
    except Exception as e:
        console.print(f"[red]Authentication failed: {e}[/red]")
        sys.exit(1)

    account = AccountConfig(
        name=account_name,
        google_list=google_list,
        tw_tag=tw_tag,
        credentials_file=credentials_file,
        exclude_uda=exclude_uda,
    )

    config.accounts[account_name] = account
    save_config(config)

    console.print(f"[green]✓ Account '{account_name}' saved.[/green]")
    console.print()
    console.print("You can now sync with:")
    console.print(f"  tw-gtasks-sync sync --account {account_name}")


@main.command("list-accounts")
@click.pass_context
def list_accounts(ctx: click.Context) -> None:
    """List all configured accounts."""
    config = load_config()

    if not config.accounts:
        console.print("[yellow]No accounts configured.[/yellow]")
        console.print("Run 'tw-gtasks-sync auth' to add an account.")
        return

    table = Table(title="Configured Accounts")
    table.add_column("Name", style="cyan")
    table.add_column("Google Tasks List", style="green")
    table.add_column("Taskwarrior Tag", style="blue")

    for name, account in config.accounts.items():
        table.add_row(
            name,
            account.google_list,
            f"+{account.tw_tag}",
        )

    console.print(table)


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize tw-gtasks-sync configuration.

    This creates the config file and configures Taskwarrior UDA.
    """
    created_config = init_config()

    if created_config:
        console.print("[green]✓ Created config file[/green]")
    else:
        console.print("[yellow]Config file already exists[/yellow]")

    if is_uda_configured():
        console.print("[yellow]UDA already configured in Taskwarrior[/yellow]")
    else:
        configure_uda()
        console.print("[green]✓ Configured Taskwarrior UDA[/green]")

    console.print()
    console.print("Configuration initialized. Next steps:")
    console.print("  1. Add an account: tw-gtasks-sync auth -a <name> -l <list> -t <tag>")
    console.print("  2. Sync: tw-gtasks-sync sync")


@main.command("remove-account")
@click.option(
    "--account",
    "-a",
    "account_name",
    required=True,
    help="Account name to remove",
)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation")
@click.pass_context
def remove_account(
    ctx: click.Context,
    account_name: str,
    force: bool,
) -> None:
    """Remove a configured account.

    This removes the account configuration but does not delete synced tasks.
    """
    config = load_config()

    if account_name not in config.accounts:
        console.print(f"[red]Account '{account_name}' not found.[/red]")
        sys.exit(1)

    account = config.accounts[account_name]

    if not force:
        console.print(f"[yellow]This will remove account '{account_name}':[/yellow]")
        console.print(f"  Google Tasks list: {account.google_list}")
        console.print(f"  Taskwarrior tag: +{account.tw_tag}")
        console.print()
        if not click.confirm("Continue?"):
            console.print("Cancelled.")
            return

    del config.accounts[account_name]
    save_config(config)

    from tw_gtasks_sync.config import get_credentials_path

    creds_path = get_credentials_path(account.credentials_file)
    if creds_path.exists():
        creds_path.unlink()

    console.print(f"[green]✓ Account '{account_name}' removed.[/green]")


if __name__ == "__main__":
    main()
