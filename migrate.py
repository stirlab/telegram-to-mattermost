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
import tempfile
import sys
import zipfile
import yaml
import subprocess
import time
import re
import logging
import argparse
import random
from pathlib import Path
from typing import List, Dict, Optional, Any, Set, Tuple
from zoneinfo import ZoneInfo
from dataclasses import dataclass


@dataclass
class MattermostConfig:
    """Configuration for Mattermost import settings."""

    chat_type: str
    users: Dict[str, str]
    import_into: Dict[str, str]
    attachment_base_dir: str
    timezone: str = "UTC"


class TelegramMattermostMigrator:
    """Handles conversion of Telegram exports to Mattermost import format."""

    # Default timeout for import jobs in seconds (1 hour)
    DEFAULT_IMPORT_TIMEOUT = 3600

    # Retry error conditions for mmctl commands
    RETRY_ERRORS = {
        "connection refused",
        "connection reset by peer",
        "connection timed out",
        "temporary failure in name resolution",
        "internal server error",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
    }

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
        "mention",
        "email",
        "text_link",
        "phone",
        "hashtag",
        "cashtag",
        "bank_card",
    }

    # ZIP file configuration
    ZIP_SUBDIRS = ("photos", "files", "video_files", "voice_messages")

    def __init__(self, config_path: str, debug: bool = False):
        """Initialize the migrator with config file path."""
        self.logger = self._setup_logging(debug)
        self.config = self._load_config(config_path)
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
                chat_type="direct_chat",  # Default value
                users=config_data.get("users", {}),
                import_into=config_data.get("import_into", {}),
                attachment_base_dir="",
                timezone=config_data.get("timezone", "UTC"),
            )

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

    def _run_mmctl(self, cmd: List[str]) -> Dict[str, Any]:
        """
        Execute mmctl command and return JSON response.
        Implements robust error handling and retry logic.
        """
        cmd.append("--json")
        max_retries = 5
        base_delay = 1
        max_delay = 30  # Maximum delay between retries in seconds
        for attempt in range(max_retries):
            try:
                self.logger.debug(f"Executing mmctl command: {' '.join(cmd)}")
                result = subprocess.run(
                    cmd, capture_output=True, text=True, check=True, encoding="utf-8"
                )
                return json.loads(result.stdout)
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.lower()
                retryable = any(err in error_msg for err in self.RETRY_ERRORS)

                if retryable and attempt < max_retries - 1:
                    # Calculate delay with exponential backoff and jitter
                    delay = min(
                        base_delay * (2**attempt) + (random.random() * 0.1), max_delay
                    )
                    self.logger.warning(
                        f"Attempt {attempt + 1}/{max_retries} failed with error: {error_msg.strip()}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    continue

                self.logger.error(f"mmctl command failed: {e.stderr}")
                self.logger.error(f"Command output: {e.stdout}")
                raise RuntimeError(f"mmctl command failed: {e.stderr}")
            except json.JSONDecodeError as e:
                self.logger.error(
                    f"Failed to parse mmctl output. Command: {' '.join(cmd)}"
                )
                raise RuntimeError(
                    f"Invalid JSON response from mmctl command: {str(e)}"
                )

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

    def _transform_text(self, text_elements: List[Any]) -> str:
        """
        Transform Telegram text formatting to Mattermost markdown.
        Handles all supported text element types with proper error handling.
        """
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
                    "Skipping text element missing required fields: "
                    f"{json.dumps(elem)}"
                )
                continue

            try:
                elem_type = elem["type"]
                elem_text = elem["text"]

                if elem_type in self.TEXT_TYPES_TO_CONVERT_TO_PLAIN_TEXT:
                    result.append(elem_text)
                elif elem_type == "code":
                    result.append(f"`{elem_text}`")
                elif elem_type == "bold":
                    result.append(f"**{elem_text}**")
                elif elem_type == "italic":
                    result.append(f"_{elem_text}_")
                elif elem_type == "underline":
                    result.append(f"**_{elem_text}_**")
                elif elem_type == "strikethrough":
                    result.append(f"~~{elem_text}~~")
                elif elem_type == "pre":
                    result.append(f"\n```\n{elem_text}\n```\n")
                elif elem_type == "mention_name":
                    if "user_id" not in elem:
                        raise ValueError("mention_name element missing user_id")
                    user_id = f"user{elem['user_id']}"
                    if user_id in self.config.users:
                        result.append(f"@{self.config.users[user_id]}")
                    else:
                        self.logger.warning(f"Unknown user ID in mention: {user_id}")
                elif elem_type == "blockquote":
                    result.append(f"\n> {elem_text}\n")
                else:
                    self.logger.warning(f"Unsupported text element type: {elem_type}")
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

        # Transform message content
        if isinstance(msg.get("text"), list):
            text = self._transform_text(msg["text"])
        elif msg.get("text") == "" and "sticker_emoji" in msg:
            text = msg["sticker_emoji"]
        else:
            text = msg.get("text", "")

        # Create message object
        mm_msg = {
            "type": msg_type,
            "id": msg.get("id"),  # Include original message ID
            msg_type: {
                "message": text,
                "user": self.config.users[msg["from_id"]],
                "create_at": self._date_to_epoch(msg["date"]),
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
                    transformed = self._transform_message(reply, replies)
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

    def _load_telegram_data(self, input_file: Optional[str]) -> Dict:
        """Load and parse Telegram export data."""
        if input_file:
            with open(input_file) as f:
                return json.load(f)
        return json.load(sys.stdin)

    def _find_top_parent(self, msg_id: int, reply_map: Dict[int, int]) -> int:
        """Recursively find the top-most parent message ID."""
        if msg_id in reply_map:
            return self._find_top_parent(reply_map[msg_id], reply_map)
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
                replies.setdefault(top_parent, []).append(msg)

        return replies

    def _convert_messages(self, messages: List[Dict], replies: Dict) -> List[str]:
        """Convert messages to Mattermost format."""
        output_lines = ['{"type":"version","version":1}']

        for msg in messages:
            if msg.get("type") == "service":
                continue

            transformed = self._transform_message(msg, replies, self.attachments)
            if transformed and "reply_to_message_id" not in msg:
                self._attach_replies(transformed, replies)
                output_lines.append(json.dumps(transformed))

        return output_lines

    def _create_zip_file(self, output_lines: List[str]) -> str:
        """Create ZIP file with messages and attachments."""
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_zip:
            self.temp_zip_path = tmp_zip.name
            self.logger.info(f"Creating temporary ZIP file: {tmp_zip.name}")

            with zipfile.ZipFile(
                tmp_zip,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                strict_timestamps=False,
            ) as zf:
                # Add directories
                for dir_name in self.ZIP_SUBDIRS:
                    zf.writestr(f"data/{dir_name}/", "")
                    self.logger.debug(f"Created directory: data/{dir_name}/")

                self._add_attachments_to_zip(zf)
                self._add_jsonl_to_zip(zf, output_lines)

            return tmp_zip.name

    def _add_attachments_to_zip(self, zf: zipfile.ZipFile) -> None:
        """Add attachments to ZIP file."""
        base_dir = Path(self.config.attachment_base_dir)
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
                "mattermost_import.jsonl",
                jsonl_content.encode("utf-8"),
                compress_type=zipfile.ZIP_DEFLATED,
            )
            self.logger.info("Added mattermost_import.jsonl to ZIP")
        except Exception as e:
            self.logger.error(f"Failed to add JSONL file: {e}")
            raise

    def _upload_to_mattermost(self, zip_path: str) -> None:
        """Upload and process ZIP file in Mattermost."""
        self.logger.info("Uploading to Mattermost...")
        result = self._run_mmctl(["mmctl", "import", "upload", zip_path])
        upload_id = result[0]["id"]

        result = self._run_mmctl(["mmctl", "import", "list", "available"])
        upload_file = next(f for f in result if f.startswith(upload_id))

        result = self._run_mmctl(["mmctl", "import", "process", upload_file])
        if result[0]["status"] != "pending":
            raise RuntimeError("Import job failed to start")

        self._monitor_import_job(result[0]["id"])

    def _monitor_import_job(self, job_id: str, timeout: int = None) -> None:
        """
        Monitor Mattermost import job status.

        Args:
            job_id: The ID of the import job to monitor
            timeout: Maximum time in seconds to wait for job completion (default: 1 hour)
        """
        timeout = timeout or self.DEFAULT_IMPORT_TIMEOUT
        start_time = time.time()
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(
                    f"Import job {job_id} timed out after {int(elapsed)} seconds"
                )

            result = self._run_mmctl(["mmctl", "import", "job", "show", job_id])
            status = result[0]["status"]

            self.logger.info(f"Import job status: {status}")

            if status == "error":
                error_details = result[0].get(
                    "error_message", "No error details available"
                )
                self.logger.error(
                    f"Mattermost import job failed. Job ID: {job_id}, Error: {error_details}"
                )
                raise RuntimeError(f"Import job failed: {error_details}")

            if status == "success":
                break

            time.sleep(1)

        self.logger.info("Import completed successfully")

    def convert(self, input_file: Optional[str] = None) -> None:
        """Convert Telegram export to Mattermost import format and upload."""
        temp_files: List[Path] = []

        try:
            # Load and parse input data
            tg_data = self._load_telegram_data(input_file)

            # Set chat type
            self.config.chat_type = (
                "direct_chat" if tg_data["type"] == "personal_chat" else "post"
            )

            # Process messages
            messages = tg_data["messages"]
            replies = self._build_reply_structure(messages)
            output_lines = self._convert_messages(messages, replies)

            # Create and upload ZIP file
            zip_path = self._create_zip_file(output_lines)
            temp_files.append(Path(zip_path))
            self._upload_to_mattermost(zip_path)

        finally:
            # Clean up all temporary files
            for temp_file in temp_files:
                try:
                    if temp_file.exists():
                        temp_file.unlink()
                        self.logger.debug(f"Cleaned up temporary file: {temp_file}")
                except Exception as e:
                    self.logger.warning(
                        f"Failed to clean up temporary file {temp_file}: {e}"
                    )


def validate_input_dir(input_dir: Path) -> Tuple[Path, Path]:
    """
    Validate input directory contains required Telegram export and config file.

    Args:
        input_dir: Path to input directory

    Returns:
        tuple containing paths to config.yaml and result.json

    Raises:
        ValueError: If directory structure is invalid or files are missing
    """
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")

    config_file = input_dir / "config.yaml"
    result_file = input_dir / "result.json"

    if not config_file.exists():
        raise ValueError(
            f"Config file not found: {config_file}\n"
            "Please create a config.yaml file in the input directory."
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
                raise ValueError("result.json does not appear to be a valid Telegram export")
    except json.JSONDecodeError:
        raise ValueError("result.json is not valid JSON")

    return config_file, result_file

def main() -> None:
    """Main entry point for the script."""
    epilog = """
Input Directory Requirements:
  The INPUT_DIR must contain:
  - A valid Telegram chat export (result.json and associated media files)
  - config.yaml: Configuration file for the Telegram to Mattermost conversion

  The Telegram export should be created using Telegram Desktop's "Export Chat History"
  feature, with JSON format selected. The config.yaml file should contain user mappings
  and other settings needed for the conversion to Mattermost format.
"""
    parser = argparse.ArgumentParser(
        description="Convert Telegram export to Mattermost import format",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "input_dir",
        help="Directory containing Telegram export and configuration files",
        type=Path
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    # If no arguments are provided, print help and exit
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()

    try:
        config_file, input_file = validate_input_dir(args.input_dir)
        migrator = TelegramMattermostMigrator(str(config_file), args.debug)
        migrator.convert(str(input_file))
    except ValueError as e:
        print(f"Error: {str(e)}\n", file=sys.stderr)
        parser.print_help()
        sys.exit(1)
    except Exception as e:
        logging.error(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
