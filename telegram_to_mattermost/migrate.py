#!/usr/bin/env python3

"""
Telegram Export to Mattermost Import conversion script.

This script converts Telegram chat exports into a format that can be imported into Mattermost.
It handles direct chats, channels, message formatting, attachments, and reply chains.

Author: Original Perl version by Axel Beckert <axel@ethz.ch>
Python conversion: 2024
Copyright 2022-2024 ETH Zurich IT Security Center

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""

import json
import datetime
import sys
import zipfile
import yaml
import re
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Any, Set, Tuple
from zoneinfo import ZoneInfo
from dataclasses import dataclass

# File names.
CONFIG_FILE_NAME = "config.yaml"
JSON_FILE_NAME = "result.json"
ZIP_FILE_NAME = "mattermost_import.zip"
IMPORT_JSONL = "import.jsonl"


@dataclass
class MattermostConfig:
    """Configuration for Mattermost import settings."""

    chat_type: str
    users: Dict[str, str]
    mentions: Dict[str, str]
    import_into: Dict[str, str]
    timezone: str = "UTC"


class TelegramMattermostMigrator:
    """Handles conversion of Telegram exports to Mattermost import format."""

    # Telegram to Mattermost type mappings
    TG_TO_MM_TYPE = {
        "message": "post",
        "personal_chat": "direct_chat",
        "private_supergroup": "channel",
        "public_supergroup": "channel",
        "private_channel": "channel",
        "public_channel": "channel",
    }

    # Constants for text conversion and formatting
    TEXT_TYPES_TO_CONVERT_TO_PLAIN_TEXT = {
        "link",
        "bot_command",
        "email",
        "text_link",
        "phone",
        "hashtag",
        "cashtag",
        "bank_card",
    }

    def _transform_basic_text(self, elem: Dict) -> str:
        """Handle plain text, links, commands, etc."""
        if elem["type"] in self.TEXT_TYPES_TO_CONVERT_TO_PLAIN_TEXT:
            return elem["text"]
        return ""

    def _transform_formatting(self, elem: Dict) -> str:
        """Handle bold, italic, underline, strikethrough."""
        elem_type = elem["type"]
        elem_text = elem["text"]
        
        format_map = {
            "code": ("`", "`"),
            "bold": ("**", "**"),
            "italic": ("_", "_"),
            "underline": ("**_", "_**"),
            "strikethrough": ("~~", "~~"),
        }
        
        if elem_type in format_map:
            prefix, suffix = format_map[elem_type]
            return f"{prefix}{elem_text}{suffix}"
        return ""

    def _transform_blocks(self, elem: Dict) -> str:
        """Handle pre and blockquote elements."""
        elem_type = elem["type"]
        elem_text = elem["text"]
        
        if elem_type == "pre":
            return f"\n```\n{elem_text}\n```\n"
        elif elem_type == "blockquote":
            return f"\n> {elem_text}\n"
        return ""

    def _transform_mentions(self, elem: Dict) -> str:
        """Handle user mentions and mapped mentions."""
        elem_type = elem["type"]
        elem_text = elem["text"]
        
        if elem_type == "mention_name":
            if "user_id" not in elem:
                raise ValueError("mention_name element missing user_id")
            user_id = f"user{elem['user_id']}"
            if user_id in self.config.users:
                return f"@{self.config.users[user_id]}"
            self.logger.warning(f"Unknown user ID in mention: {user_id}")
        elif elem_type == "mention":
            mention_text = elem_text.lstrip("@")
            if self.config.mentions and mention_text in self.config.mentions:
                return f"@{self.config.mentions[mention_text]}"
            if self.config.mentions:
                self.logger.debug(f"No mapping found for mention: {elem_text}")
            return elem_text
        return ""

    # ZIP file configuration
    ZIP_SUBDIRS = ("photos", "files", "video_files", "voice_messages")

    def __init__(
        self,
        input_dir: Path,
        output_file: Path,
        config_file: str = CONFIG_FILE_NAME,
        conversation_log: Optional[Path] = None,
        debug: bool = False,
    ):
        """
        Initialize the migrator with config file path and input directory.

        Args:
            input_dir: Path to the directory containing Telegram export
            output_file: Path where to write the output ZIP file
            config_file: Name of config file to use (default: config.yaml)
            conversation_log: Path to conversation log
            debug: Enable debug logging if True
        """
        self.logger = self._setup_logging(debug)
        self.input_dir = input_dir
        self.output_file = output_file
        self.config_file = config_file
        self.conversation_log = conversation_log
        self.debug = debug
        self.config_path = self.input_dir / self.config_file
        self.config = self._load_config(self.config_path)
        self.attachments: Set[str] = set()

    def _setup_logging(self, debug: bool) -> logging.Logger:
        """Configure logging with appropriate level and format."""
        logger = logging.getLogger(__name__)
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        return logger

    def _load_config(self, config_path: str) -> MattermostConfig:
        """Load and validate configuration from YAML file."""
        try:
            with open(config_path) as f:
                config_data = yaml.safe_load(f)
            config = MattermostConfig(
                chat_type=config_data.get("chat_type", "direct_chat"),
                users=config_data.get("users"),
                mentions=config_data.get("mentions"),
                import_into=config_data.get("import_into"),
                timezone=config_data.get("timezone", "UTC"),
            )
            if not config.users:
                raise KeyError("Missing required field 'users' in config")
            if config.chat_type != "direct_chat" and not config.import_into:
                raise KeyError("Missing required field 'import_into' in config")

            # Validate timezone
            try:
                ZoneInfo(config.timezone)
            except KeyError as e:
                raise ValueError(
                    f"Invalid timezone '{config.timezone}'. Please use a valid IANA timezone name."
                ) from e
            except Exception as e:
                raise ValueError(
                    f"Error validating timezone '{config.timezone}': {str(e)}"
                ) from e

            return config
        except Exception as e:
            self.logger.error(f"Failed to load config: {e}")
            raise

    def _date_to_epoch(self, tg_time: str) -> int:
        """Convert Telegram timestamp to Mattermost millisecond epoch."""
        dt = datetime.datetime.fromisoformat(tg_time)
        tz = ZoneInfo(self.config.timezone)
        dt = dt.replace(tzinfo=tz)
        return int(dt.timestamp() * 1000)

    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename for safe filesystem operations.
        Preserves directory structure while sanitizing individual path components.
        """
        path = Path(filename)
        parts = []
        for part in path.parts:
            # Sanitize each path component individually
            safe_part = re.sub(r"[^A-Za-z0-9_@=+:.,\-]", "_", part)
            # Ensure no empty parts
            if safe_part:
                parts.append(safe_part)

        # Reconstruct path with sanitized components
        return str(Path(*parts))

    def _get_message_text(self, msg: Dict) -> str:
        """Extract and transform message text content consistently."""
        if isinstance(msg.get("text"), list):
            return self._transform_text(msg["text"])
        elif msg.get("text") == "" and "sticker_emoji" in msg:
            return msg["sticker_emoji"]
        else:
            return msg.get("text", "")

    def _transform_text(self, text_elements: List[Any]) -> str:
        """Transform Telegram text formatting to Mattermost markdown."""
        result = []

        for elem in text_elements:
            if isinstance(elem, str):
                result.append(elem)
                continue

            if not isinstance(elem, dict):
                self.logger.warning(f"Skipping invalid text element: {elem}")
                continue

            if "type" not in elem or "text" not in elem:
                self.logger.warning(
                    f"Skipping text element missing required fields: {json.dumps(elem)}"
                )
                continue

            try:
                # Try each transformation type in order
                transformed = (
                    self._transform_basic_text(elem)
                    or self._transform_formatting(elem)
                    or self._transform_blocks(elem)
                    or self._transform_mentions(elem)
                )
                if transformed:
                    result.append(transformed)
                else:
                    self.logger.warning(f"Unsupported text element type: {elem['type']}")
                
            except Exception as e:
                self.logger.error(
                    f"Error processing text element {json.dumps(elem)}: {str(e)}"
                )
                continue

        return "".join(result)

    def _transform_message(
        self, msg: Dict[str, Any], attachments: Set[str]
    ) -> Optional[Dict[str, Any]]:
        """Transform a Telegram message to Mattermost format."""
        if "type" not in msg:
            return None

        is_direct = self.config.chat_type == "direct_chat"
        msg_type = "direct_post" if is_direct else self.TG_TO_MM_TYPE.get(msg["type"])

        if not msg_type:
            self.logger.warning(f"Unsupported message type: {msg['type']}")
            return None

        if msg["from_id"] not in self.config.users:
            self.logger.warning(
                f"Unknown user ID {msg['from_id']} not found in config.users mapping. "
                f"Message from {msg.get('date', 'unknown date')} with content '{msg.get('text', '[no text]')}' will be skipped. "
                "Please update your users mapping in the configuration file."
            )
            return None

        text = self._get_message_text(msg)

        # Create message object
        mm_msg = {
            "type": msg_type,
            "id": msg.get("id"),  # Include original message ID
            msg_type: {
                "message": text,
                "user": self.config.users[msg["from_id"]],
                "create_at": self._date_to_epoch(msg["date"]),
                "edit_at": self._date_to_epoch(msg["edited"]) if "edited" in msg else 0,
            },
        }

        # Add channel/team info
        if is_direct:
            mm_msg[msg_type]["channel_members"] = list(self.config.users.values())
        else:
            mm_msg[msg_type].update(
                {
                    "channel": self.config.import_into["channel"],
                    "team": self.config.import_into["team"],
                }
            )

        # Handle attachments
        for attach_type in ("file", "photo"):
            if attach_type in msg and not (
                attach_type == "file" and msg.get("media_type") == "sticker"
            ):
                attachment_path = msg[attach_type]
                mm_msg[msg_type].setdefault("attachments", []).append(
                    {"path": attachment_path}
                )
                mm_msg[msg_type].setdefault("props", {"attachments": []})
                attachments.add(attachment_path)

        return mm_msg

    def _attach_replies(
        self, msg: Dict[str, Any], replies: Dict[int, List[Dict[str, Any]]]
    ) -> None:
        """
        Attach reply chains to messages with comprehensive error handling.
        Maintains the reply chain structure while ensuring data consistency.
        """
        msg_type = msg["type"]
        if msg_type not in ("post", "direct_post"):
            self.logger.debug(f"Skipping replies for message type: {msg_type}")
            return

        msg_id = msg.get("id")
        if not msg_id:
            self.logger.debug("Message has no ID, skipping replies")
            return

        if msg_id not in replies:
            self.logger.debug(f"No replies found for message {msg_id}")
            return

        try:
            msg[msg_type]["replies"] = []
            reply_count = 0

            for reply in replies[msg_id]:
                try:
                    transformed = self._transform_message(reply, self.attachments)
                    if not transformed:
                        self.logger.warning(
                            f"Failed to transform reply {reply.get('id')} "
                            f"for message {msg_id}"
                        )
                        continue

                    reply_content = transformed[msg_type]
                    # Remove channel/team info from replies
                    for key in ("channel", "team"):
                        reply_content.pop(key, None)

                    msg[msg_type]["replies"].append(reply_content)
                    reply_count += 1

                except Exception as e:
                    self.logger.error(
                        f"Error processing reply {reply.get('id')} "
                        f"for message {msg_id}: {e}"
                    )
                    continue

            self.logger.debug(f"Attached {reply_count} replies to message {msg_id}")

        except Exception as e:
            self.logger.error(f"Failed to attach replies to message {msg_id}: {e}")

    def _load_telegram_data(self, input_file: str) -> Dict:
        """Load and parse Telegram export data."""
        with open(input_file) as f:
            return json.load(f)

    def _find_top_parent(
        self, msg_id: int, reply_map: Dict[int, int], visited: Optional[Set[int]] = None
    ) -> int:
        """
        Recursively find the top-most parent message ID.

        Args:
            msg_id: Current message ID
            reply_map: Dictionary mapping message IDs to their parent IDs
            visited: Set of already visited message IDs (to detect cycles)

        Returns:
            The top-most parent message ID
        """
        if visited is None:
            visited = set()

        # If we've seen this message before, we have a cycle
        if msg_id in visited:
            return msg_id

        visited.add(msg_id)

        if msg_id in reply_map:
            return self._find_top_parent(reply_map[msg_id], reply_map, visited)
        return msg_id

    def _build_reply_structure(
        self, messages: List[Dict[str, Any]]
    ) -> Dict[int, List[Dict[str, Any]]]:
        """Build the reply chain structure from messages."""
        reply_to: Dict[int, int] = {}
        message_by_id: Dict[int, Dict] = {}
        replies: Dict[int, List[Dict]] = {}

        # Track forward chain first
        for msg in messages:
            if "id" in msg:
                message_by_id[msg["id"]] = msg
                if "reply_to_message_id" in msg:
                    reply_to[msg["id"]] = msg["reply_to_message_id"]

        # Build final reply structure
        for msg in messages:
            if "reply_to_message_id" in msg:
                top_parent = self._find_top_parent(msg["reply_to_message_id"], reply_to)
                # Only add reply if the parent message exists in our message set
                if top_parent in message_by_id:
                    replies.setdefault(top_parent, []).append(msg)

        return replies

    def _convert_messages(self, messages: List[Dict], replies: Dict) -> List[str]:
        """Convert messages to Mattermost format."""
        output_lines = ['{"type":"version","version":1}']

        for msg in messages:
            if msg.get("type") == "service":
                continue

            transformed = self._transform_message(msg, self.attachments)
            if transformed and "reply_to_message_id" not in msg:
                self._attach_replies(transformed, replies)
                output_lines.append(json.dumps(transformed))

        return output_lines

    def _create_zip_file(self, output_lines: List[str]) -> None:
        """Create ZIP file with messages and attachments at the specified path."""
        self.logger.info(f"Creating ZIP file: {self.output_file}")

        with zipfile.ZipFile(
            self.output_file,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            strict_timestamps=False,
        ) as zf:
            for dir_name in self.ZIP_SUBDIRS:
                zf.writestr(f"data/{dir_name}/", "")
                self.logger.debug(f"Created directory: data/{dir_name}/")

            self._add_attachments_to_zip(zf)
            self._add_jsonl_to_zip(zf, output_lines)

    def _add_attachments_to_zip(self, zf: zipfile.ZipFile) -> None:
        """Add attachments to ZIP file."""
        base_dir = self.input_dir
        for attachment in self.attachments:
            attach_path = base_dir / attachment
            if not attach_path.exists():
                self.logger.warning(f"Skipping missing attachment: {attachment}")
                continue
            try:
                safe_path = self._sanitize_filename(attachment)
                zip_path = f"data/{safe_path}"

                self.logger.info(f"Adding attachment: {safe_path}")
                zf.write(attach_path, zip_path, compress_type=zipfile.ZIP_STORED)
            except Exception as e:
                self.logger.error(f"Failed to add attachment {attachment}: {e}")

    def _add_jsonl_to_zip(self, zf: zipfile.ZipFile, output_lines: List[str]) -> None:
        """Add JSONL content to ZIP file."""
        try:
            jsonl_content = "\n".join(output_lines)
            zf.writestr(
                IMPORT_JSONL,
                jsonl_content.encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
            )
            self.logger.info("Added mattermost_import.jsonl to ZIP")
        except Exception as e:
            self.logger.error(f"Failed to add JSONL file: {e}")
            raise

    def _write_conversation_log(
        self, messages: List[Dict], replies: Dict[int, List[Dict]]
    ) -> None:
        """Write a text-only log of the conversation."""
        if not self.conversation_log:
            return

        # Build a map of message IDs to their full text for reply lookups
        msg_texts: Dict[int, str] = {}

        def format_message_text(msg: Dict) -> str:
            """Format the message text including any attachments."""
            text = []

            text.append(self._get_message_text(msg))

            # Add attachments
            for attach_type in ("file", "photo"):
                if attach_type in msg and not (
                    attach_type == "file" and msg.get("media_type") == "sticker"
                ):
                    attachment_path = Path(msg[attach_type])
                    text.append(f"[{attach_type.upper()}: {attachment_path.name}]")

            return "\n".join(filter(None, text))

        def format_message(msg: Dict, indent: str = "") -> str:
            """Format a single message with timestamp and username."""
            if msg["type"] == "service":
                return ""

            if msg["from_id"] not in self.config.users:
                return ""

            timestamp = datetime.datetime.fromisoformat(msg["date"]).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            username = self.config.users[msg["from_id"]]

            text = format_message_text(msg)
            if not text:
                return ""

            # Store message text for reply lookups
            if "id" in msg:
                msg_texts[msg["id"]] = text.split("\n")[
                    0
                ]  # Store first line for reply context

            # Format reply if this message is a reply
            if "reply_to_message_id" in msg and msg["reply_to_message_id"] in msg_texts:
                original = msg_texts[msg["reply_to_message_id"]]
                text = f"> @{username}: {original}\n{text}"

            return f"{indent}[{timestamp}] @{username}:\n{indent}{text}"

        try:
            with open(self.conversation_log, "w", encoding="utf-8") as f:
                self.logger.info(f"Writing conversation log to: {self.conversation_log}")
                # Write legend
                f.write(
                    """CONVERSATION LOG LEGEND
----------------------
Message format: [timestamp] @username: message
Reply format: Messages starting with '>' are replies, multiple '>' indicate reply depth
              Example: '>' = direct reply, '>>' = reply to reply
Attachments: Indicated in brackets with type and filename
  [PHOTO: sunset.jpg]
  [VIDEO: meeting_recap.mp4]
  [FILE: report.pdf]
  [VOICE: message.ogg]
----------------------

"""
                )

                # Write messages
                for msg in messages:
                    formatted = format_message(msg)
                    if formatted:
                        f.write(f"{formatted}\n\n")
                
                self.logger.info(f"Successfully wrote conversation log to: {self.conversation_log}")

        except Exception as e:
            self.logger.error(f"Failed to write conversation log: {e}")

    def convert(self) -> None:
        """
        Convert Telegram export to Mattermost import format.

        Args:
            output_file: Optional path for output ZIP file. If None, uses 'mattermost_import.zip'
        """
        # Load and parse input data from result.json in input directory
        input_file = self.input_dir / JSON_FILE_NAME
        tg_data = self._load_telegram_data(str(input_file))

        # Set chat type
        self.config.chat_type = (
            "direct_chat" if tg_data["type"] == "personal_chat" else "post"
        )

        # Process messages
        messages = tg_data["messages"]
        replies = self._build_reply_structure(messages)

        # Write conversation log if requested
        if self.conversation_log:
            self._write_conversation_log(messages, replies)

        output_lines = self._convert_messages(messages, replies)

        # Create ZIP file
        self._create_zip_file(output_lines)
        self.logger.info(f"Created Mattermost import file: {self.output_file}")


def validate_input_dir(
    input_dir: Path, config_file: str = CONFIG_FILE_NAME
) -> Tuple[Path, Path]:
    """
    Validate input directory contains required Telegram export and config file.

    Args:
        input_dir: Path to input directory
        config_file: Name of config file to validate (default: config.yaml)

    Returns:
        tuple containing paths to config.yaml and result.json

    Raises:
        ValueError: If directory structure is invalid or files are missing
    """
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")

    config_file_path = input_dir / config_file
    result_file = input_dir / JSON_FILE_NAME

    if not config_file_path.exists():
        raise ValueError(
            f"Config file not found: {config_file_path}\n"
            f"Please create a {config_file} file in the input directory."
        )

    if not result_file.exists():
        raise ValueError(
            f"Telegram export file not found: {result_file}\n"
            "The input directory must contain a valid Telegram chat export "
            "created using Telegram Desktop's 'Export Chat History' feature."
        )

    # Verify this appears to be a valid Telegram export
    try:
        with open(result_file) as f:
            data = json.load(f)
            if "type" not in data or "messages" not in data:
                raise ValueError(
                    "result.json does not appear to be a valid Telegram export"
                )
    except json.JSONDecodeError:
        raise ValueError("result.json is not valid JSON")

    return config_file_path, result_file


def main() -> None:
    """Main entry point for the script."""
    epilog = """
Input Directory Requirements:
  The INPUT_DIR must contain:
  - A valid Telegram chat export (result.json and associated media files)
  - config.yaml: Configuration file for the Telegram to Mattermost conversion
                (or specified config file if using --config-file option)

  The Telegram export should be created using Telegram Desktop's "Export Chat History"
  feature, with JSON format selected. The config.yaml file should contain user mappings
  and other settings needed for the conversion to Mattermost format.
"""
    parser = argparse.ArgumentParser(
        description="Convert Telegram export to Mattermost import format",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_dir",
        help="Directory containing Telegram export and configuration files",
        type=Path,
    )
    parser.add_argument(
        "--output-file",
        "-o",
        type=Path,
        help="Output ZIP file path (default: %(default)s)",
        default=ZIP_FILE_NAME,
    )
    parser.add_argument(
        "--config-file",
        "-c",
        help=f"Configuration file name (default: {CONFIG_FILE_NAME})",
        default=CONFIG_FILE_NAME,
    )
    parser.add_argument(
        "--conversation-log",
        type=Path,
        help="Write a text-only log of the conversation to this file",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()

    try:
        validate_input_dir(args.input_dir, config_file=args.config_file)
        migrator = TelegramMattermostMigrator(
            Path(args.input_dir),
            Path(args.output_file),
            args.config_file,
            args.conversation_log,
            args.debug,
        )
        migrator.convert()
    except ValueError as e:
        print(f"Error: {str(e)}\n", file=sys.stderr)
        parser.print_help()
        sys.exit(1)
    except Exception as e:
        logging.error(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
