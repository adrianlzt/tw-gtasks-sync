"""Configuration management for tw-gtasks-sync."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from xdg_base_dirs import xdg_config_home, xdg_data_home

APP_NAME = "tw-gtasks-sync"
GTASKS_ID_UDA = "gtasks_id"
GTASKS_LIST_UDA = "gtasks_list"

DEFAULT_TASKRC_PATH = Path.home() / ".taskrc"


@dataclass
class AccountConfig:
    """Configuration for a single sync account."""

    name: str
    google_list: str
    tw_tag: str
    credentials_file: str = ""
    exclude_uda: str | None = None

    def __post_init__(self) -> None:
        if not self.credentials_file:
            self.credentials_file = f"credentials_{self.name}.pickle"


@dataclass
class AppConfig:
    """Application configuration."""

    accounts: dict[str, AccountConfig] = field(default_factory=dict)
    conflict_strategy: str = "notify"
    default_oauth_port: int = 8081

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        """Create AppConfig from dictionary."""
        accounts = {}
        for name, acct_data in data.get("accounts", {}).items():
            accounts[name] = AccountConfig(
                name=name,
                google_list=acct_data.get("google_list", ""),
                tw_tag=acct_data.get("tw_tag", ""),
                credentials_file=acct_data.get(
                    "credentials_file", f"credentials_{name}.pickle"
                ),
                exclude_uda=acct_data.get("exclude_uda"),
            )
        return cls(
            accounts=accounts,
            conflict_strategy=data.get("conflict_strategy", "notify"),
            default_oauth_port=data.get("default_oauth_port", 8081),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        accounts_data = {}
        for name, acct in self.accounts.items():
            acct_dict = {
                "google_list": acct.google_list,
                "tw_tag": acct.tw_tag,
                "credentials_file": acct.credentials_file,
            }
            if acct.exclude_uda:
                acct_dict["exclude_uda"] = acct.exclude_uda
            accounts_data[name] = acct_dict
        return {
            "accounts": accounts_data,
            "conflict_strategy": self.conflict_strategy,
            "default_oauth_port": self.default_oauth_port,
        }


def get_config_dir() -> Path:
    """Get the configuration directory."""
    config_dir = xdg_config_home() / APP_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_data_dir() -> Path:
    """Get the data directory (for credentials, mappings, etc.)."""
    data_dir = xdg_data_home() / APP_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_config_path() -> Path:
    """Get the path to the configuration file."""
    return get_config_dir() / "config.yaml"


def get_credentials_path(credentials_file: str) -> Path:
    """Get the full path for a credentials file."""
    return get_data_dir() / credentials_file


def get_mappings_dir() -> Path:
    """Get the directory for storing ID mappings."""
    mappings_dir = get_data_dir() / "mappings"
    mappings_dir.mkdir(parents=True, exist_ok=True)
    return mappings_dir


def get_mapping_path(account_name: str) -> Path:
    """Get the path for storing ID mappings for an account."""
    return get_mappings_dir() / f"{account_name}.yaml"


def load_config() -> AppConfig:
    """Load configuration from file."""
    config_path = get_config_path()

    if not config_path.exists():
        return AppConfig()

    with config_path.open("r") as f:
        data = yaml.safe_load(f) or {}

    return AppConfig.from_dict(data)


def save_config(config: AppConfig) -> None:
    """Save configuration to file."""
    config_path = get_config_path()

    with config_path.open("w") as f:
        yaml.dump(config.to_dict(), f, default_flow_style=False)


def get_default_config_content() -> str:
    """Get default configuration content for new setups."""
    return """# tw-gtasks-sync configuration
#
# Define accounts to sync. Each account maps a Google Tasks list
# to a Taskwarrior tag.
#
# accounts:
#   personal:
#     google_list: "taskwarrior"  # Name of Google Tasks list
#     tw_tag: "personal"          # Taskwarrior tag (without +)
#   work:
#     google_list: "Work Tasks"
#     tw_tag: "work"
#
# conflict_strategy: "notify"  # How to handle conflicts: notify, skip, prefer_tw, prefer_gtasks
# default_oauth_port: 8081      # Port for OAuth callback server

accounts: {}

conflict_strategy: "notify"
default_oauth_port: 8081
"""


def init_config() -> bool:
    """Initialize configuration file if it doesn't exist.

    Returns True if a new config was created, False if it already existed.
    """
    config_path = get_config_path()

    if config_path.exists():
        return False

    config_path.write_text(get_default_config_content())
    return True


def get_taskrc_path() -> Path:
    """Get the path to the Taskwarrior config file."""
    import os

    taskrc_env = os.environ.get("TASKRC")
    if taskrc_env:
        return Path(taskrc_env)

    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        xdg_taskrc = Path(config_home) / "task" / "taskrc"
        if xdg_taskrc.exists():
            return xdg_taskrc

    return DEFAULT_TASKRC_PATH


def is_uda_configured(taskrc_path: Path | None = None) -> bool:
    """Check if the UDA is configured in Taskwarrior."""
    if taskrc_path is None:
        taskrc_path = get_taskrc_path()

    if not taskrc_path.exists():
        return False

    content = taskrc_path.read_text()
    return f"uda.{GTASKS_ID_UDA}" in content


def configure_uda(taskrc_path: Path | None = None) -> bool:
    """Add UDA configuration to Taskwarrior config.

    Returns True if UDA was added, False if already configured.
    """
    if taskrc_path is None:
        taskrc_path = get_taskrc_path()

    if is_uda_configured(taskrc_path):
        return False

    uda_config = f"""

# tw-gtasks-sync UDA
uda.{GTASKS_ID_UDA}.type=string
uda.{GTASKS_ID_UDA}.label=Google Tasks ID
"""

    with taskrc_path.open("a") as f:
        f.write(uda_config)

    return True
