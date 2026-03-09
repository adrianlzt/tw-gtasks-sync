# tw-gtasks-sync

Synchronize Google Tasks with Taskwarrior bidirectionally.

## Features

- **Bidirectional sync**: Tasks created or modified in either Google Tasks or Taskwarrior are synchronized
- **Multiple accounts**: Sync different Google accounts with different Taskwarrior tags
- **OAuth authentication**: No need for service accounts or API keys
- **Conflict handling**: Desktop notifications and console output with resolution steps
- **UDA tracking**: Uses Taskwarrior User Defined Attributes for reliable mapping
- **Task filtering**: Exclude tasks with specific UDAs (e.g., Jira-synced tasks)
- **Rich task data**: Syncs project, tags, and annotations as Google Tasks notes

## Installation

### With uvx (recommended)

```bash
uvx install tw-gtasks-sync
```

### With pip

```bash
pip install tw-gtasks-sync
```

### With pipx

```bash
pipx install tw-gtasks-sync
```

## Requirements

- Python 3.10 or higher
- Taskwarrior 2.6+
- A Google account

## Quick Start

### 1. Initialize

```bash
tw-gtasks-sync init
```

This creates the configuration file and sets up Taskwarrior UDA.

### 2. Add an account

```bash
tw-gtasks-sync auth --account personal --google-list "taskwarrior" --tw-tag "personal"
```

This will:
1. Open your browser for Google OAuth authentication
2. Save the credentials
3. Create the sync configuration

### 3. Sync

```bash
# Sync a specific account
tw-gtasks-sync sync --account personal

# Sync all configured accounts
tw-gtasks-sync sync

# Force update all tasks (ignores cache)
tw-gtasks-sync sync --force
```

## Configuration

Configuration is stored in `~/.config/tw-gtasks-sync/config.yaml`:

```yaml
accounts:
  personal:
    google_list: "taskwarrior"
    tw_tag: "personal"
    credentials_file: "credentials_personal.pickle"
  work:
    google_list: "Work Tasks"
    tw_tag: "work"
    credentials_file: "credentials_work.pickle"
    exclude_uda: "jiraid"  # Exclude tasks with this UDA set

conflict_strategy: "notify"
default_oauth_port: 8081
```

## Excluding Tasks

Use `exclude_uda` to exclude tasks that have a specific UDA set. This is useful for excluding tasks that are synced from other sources (e.g., Jira):

```bash
# Via CLI
tw-gtasks-sync auth --account work --google-list "Work" --tw-tag work --exclude-uda jiraid

# Or edit config.yaml directly
```

Tasks with the `jiraid` UDA will be skipped during sync.

## Taskwarrior UDA

The app uses a User Defined Attribute (UDA) to track Google Tasks IDs:

```
uda.gtasks_id.type=string
uda.gtasks_id.label=Google Tasks ID
```

This is automatically configured when you run `tw-gtasks-sync init`.

## Task Data Synced

### Taskwarrior → Google Tasks

- **Title**: Task description
- **Status**: pending/completed
- **Due date**: If set
- **Notes**: Includes project, tags (excluding sync tag), and annotations

Example notes:
```
Project: myproject
Tags: +urgent, +bug
• First annotation
• Second annotation
```

### Google Tasks → Taskwarrior

- **Description**: Task title
- **Status**: pending/completed
- **Due date**: If set
- **Project**: Extracted from notes
- **Tags**: Extracted from notes (plus sync tag)
- **Annotations**: Extracted from notes

## Conflict Resolution

When a task is modified on both Google Tasks and Taskwarrior since the last sync:

```
⚠️  CONFLICT: 'task name'
   Account: personal
   TW modified: 2026-03-09 07:13:51+00:00
   GTasks modified: 2026-03-09 07:14:13+00:00
   Skipping sync for this task.
   To resolve:
     1. Edit task in Taskwarrior: task <uuid> edit
     2. Or edit in Google Tasks, then run sync again
     3. Or force TW -> GTasks: tw-gtasks-sync sync --force
```

Options to resolve:
1. Edit the task in one place and sync again
2. Use `--force` to overwrite Google Tasks with Taskwarrior data

## Using Your Own Google API Credentials

By default, the app uses bundled OAuth credentials. If you hit rate limits or want your own:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable the Tasks API
4. Configure OAuth consent screen (External, add yourself as test user)
5. Create OAuth credentials (Desktop app)
6. Download the JSON file
7. Save it as `~/.config/tw-gtasks-sync/client_secret.json`

## Automation

### Cron

```cron
# Sync every 10 minutes
*/10 * * * * tw-gtasks-sync sync
```

### Systemd Timer

Create `~/.config/systemd/user/tw-gtasks-sync.timer`:

```ini
[Unit]
Description=Sync Google Tasks with Taskwarrior

[Timer]
OnBootSec=5m
OnUnitActiveSec=10m

[Install]
WantedBy=timers.target
```

Create `~/.config/systemd/user/tw-gtasks-sync.service`:

```ini
[Unit]
Description=Sync Google Tasks with Taskwarrior

[Service]
Type=oneshot
ExecStart=%h/.local/bin/tw-gtasks-sync sync
```

Enable:

```bash
systemctl --user enable --now tw-gtasks-sync.timer
```

## Commands

### `tw-gtasks-sync init`

Initialize configuration and Taskwarrior UDA.

### `tw-gtasks-sync auth`

Authenticate a Google account and create a sync configuration.

```bash
tw-gtasks-sync auth --account NAME --google-list LIST --tw-tag TAG [--exclude-uda UDA]
```

Options:
- `-a, --account`: Name for this sync configuration (required)
- `-l, --google-list`: Google Tasks list name (required)
- `-t, --tw-tag`: Taskwarrior tag without the `+` (required)
- `--exclude-uda`: Exclude tasks that have this UDA set
- `--oauth-port`: Port for OAuth callback (default: 8081)

### `tw-gtasks-sync sync`

Synchronize tasks.

```bash
tw-gtasks-sync sync [--account NAME] [--force]
```

Options:
- `-a, --account`: Sync a specific account (syncs all if not specified)
- `-f, --force`: Force update all tasks (ignores cache)
- `-v, --verbose`: Show detailed output

### `tw-gtasks-sync list-accounts`

List all configured accounts.

### `tw-gtasks-sync remove-account`

Remove an account configuration.

## Reset Sync

To completely reset sync state:

```bash
# Delete local sync data (keeps auth & config)
rm -rf ~/.local/share/tw-gtasks-sync/mappings/
rm -rf ~/.local/share/tw-gtasks-sync/serdes/

# Optionally clean TW UDA
task +<tag> modify gtasks_id:

# Then delete the list in Google Tasks manually

# Re-sync
tw-gtasks-sync sync --account <name>
```

## License

MIT
