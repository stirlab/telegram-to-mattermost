# telegram-to-mattermost

## Description

Converts [Telegram](https://telegram.org/) exports into an import format suitable for [Mattermost](https://mattermost.com/). It supports:

- Importing Telegram channels/supergroups into Mattermost channels
- Importing Telegram personal chats into Mattermost direct messages

The tool is currently working well (tested with versions between Mattermost 7.5.1 and at least 10.2.1) but so far does not support all functionality which might be used on Telegram.

### Working

- Support for importing Telegram Channels/Supergroups/Megagroups into Mattermost Channels.
- Support for replies
- Support for attachments (images, videos, PDF, etc.)
- Workaround for Telegram's export neither containing timezones nor DST flags. (This has been fixed in Telegram exports recently by also including the date in timezone independent Unix timestamps. `telegram-to-mattermost` does though currently not rely on them and hence still can import older Telegram exports without this additional field.)
- Support for importing Telegram Personal Chats into Mattermost Direct Channels.
- Support for voice messages (as attachments)
- Support for video messages (as attachments)

### Untested

- Importing non-private Supergroups. (Probably just works, but might be non-trivial wrt. to user management.)
- Importing Telegram Forums, Basic Groups or Gigagroups.
- Bots in groups

### Not implemented

- Support for emoji reactions—due to [emoji reactions are missing in JSON exports](https://github.com/telegramdesktop/tdesktop/issues/16890).
- Channel creation

### Not planned to be supported

- Import of users
- Pinned messages.

## Requirements

* Python 3.9 or higher

* Python packages:
  * PyYAML
  * Other dependencies from Python standard library

* [Mattermost](https://mattermost.com/) server:
  * Mattermost 7.5.1 or higher recommended for proper attachment support
  * For attachments, configure appropriate file size limits:
    ```
    # In /opt/mattermost/config/config.defaults.json:
    "MaxFileSize": 104857600,
    ```
    ```
    # In /etc/nginx/conf.d/mattermost.conf if using Nginx:
    client_max_body_size 1G;
    ```

* [Telegram Desktop](https://desktop.telegram.org/) application for creating the export

## Installation

```bash
# Install package in development mode
pip install -e ".[dev]"

# Or install for regular usage
pip install .
```

## Usage

After installation, you can use the command-line tool:

```bash
# Basic usage
telegram-to-mattermost /path/to/telegram_export

# Enable debug logging
telegram-to-mattermost --debug /path/to/telegram_export

# Specify custom output file (default is mattermost_import.zip)
telegram-to-mattermost -o custom_output.zip /path/to/telegram_export

# Use a custom configuration file (default is config.yaml)
telegram-to-mattermost -c custom_config.yaml /path/to/telegram_export
```

## Configuration

### Configuration file

`telegram-to-mattermost` requires a [YAML](https://yaml.org/) configuration file named `config.yaml` (or specified with --config-file) to be placed in the same directory as your Telegram export's `result.json` file.

#### Example configuration file

```yaml
# telegram-to-mattermost import configuration
---
# Required.
# For channel imports, include all users in the channel
# For direct message imports, include only users in the direct message group.
users:
  # In the following form:
  # telegram_user_id: mattermost_username
  user1234: annaiscool
  user4321: bertarocks
  user5678: charly
# Required for any chat type that is not direct_chat
import_into:
  team: your-team-name
  channel: town-square
# Optional: Map @mentions to a specific @mention in  Mattermost
# Note that Telegram 'mention name' text entities are handled by the 'users' mapping above,
# this is for any other @mentions.
# Format is the mention string without the @ symbol, then the Mattermost entity to mention.
mentions:
  something: someotherthing
# Optional: Defaults to UTC.
timezone: America/New_York
# Optional: One of post, channel, direct_chat. Defaults to direct_chat
chat_type: channel
```

#### Configuration File Explanation

The `user<telegram_user_id>` keywords are the values in the `from_id` JSON field in the chat export file. The value behind them is the user name (without the `@`) of the according person on the Mattermost server.

Lines which have a `#` as first non-blank character are comments.

The timezone is the timezone which should be assumed for the time stamps in Telegram's chat export.  If no timezone is given, UTC is assumed.

### Chat Export File

The chat export file is what you get when you click on the per-group-chat menu (top right three dots menu in the [Telegram Desktop](https://desktop.telegram.org/) application) and click on "Export Chat History" (or similar depending on localisation) and choose the "JSON" format.

The export feature has been [introduced in Telegram Desktop 1.3.13 on 27th of August 2018](https://telegram.org/blog/export-and-more). `telegram-to-mattermost` though has only been tested with Telegram Desktop versions 3.5.2, 3.6.0, 3.7.3, 4.3.1, and 5.9 so far. We though assume that exports from all Telegram Desktop versions since at least 3.5.0 work fine with `telegram-to-mattermost`.

### Importing into Mattermost

This script produces a ZIP file suitable for import into a Mattermost server. See [Mattermost documentation](https://docs.mattermost.com) for instructions on how to handle upload/import into a Mattermost server.

## Author, License and Copyright

Original Perl version: Axel Beckert <axel@ethz.ch>
Python conversion: Chad Phillips, with great assistance from [Aider](https://aider.chat) and [Claude 3.5 Sonnet](https://www.anthropic.com/claude/sonnet)
Copyright 2022-2024 ETH Zurich IT Security Center

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with this program.  If not, see https://www.gnu.org/licenses/.
