"""Basic functionality tests for telegram-to-mattermost converter."""

import pytest
from pathlib import Path
import yaml
import zipfile
from telegram_to_mattermost.migrate import (
    TelegramMattermostMigrator,
    validate_input_dir,
)


def test_config_loading(test_data_dir):
    """Test loading the example configuration."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))
    assert migrator.config.users == {
        "user123": "abc",
        "user789": "ghi",
        "user456": "def",
    }
    assert migrator.config.import_into == {"team": "example", "channel": "town square"}


def test_date_conversion(test_data_dir):
    """Test date to epoch conversion."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))
    assert migrator._date_to_epoch("2022-03-25T17:30:36") == 1648229436000


def test_channel_message_transformation(test_data_dir):
    """Test basic message transformation."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": "Morning!",
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed == {
        "type": "post",
        "id": 123456,
        "post": {
            "channel": "town square",
            "team": "example",
            "user": "abc",
            "message": "Morning!",
            "create_at": 1647324371000,
            "edit_at": 0,
        },
    }


def test_direct_message_transformation(test_data_dir):
    """Test basic message transformation."""
    migrator = TelegramMattermostMigrator(
        test_data_dir, Path("output.zip"), "direct_config.yaml"
    )

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": "Morning!",
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed == {
        "type": "direct_post",
        "id": 123456,
        "direct_post": {
            "channel_members": ["abc", "def", "ghi"],
            "user": "abc",
            "message": "Morning!",
            "create_at": 1647324371000,
            "edit_at": 0,
        },
    }


def test_complex_message_transformation(test_data_dir):
    """Test transformation of messages with complex formatting."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": [
            {"text": "/me", "type": "bot_command"},
            " says ",
            {"text": "something italic", "type": "italic"},
            " to ",
            {"text": "Anna", "user_id": 123, "type": "mention_name"},
            " with umläuts and ",
            {"text": "boldly emphasized", "type": "bold"},
            " text",
        ],
    }

    transformed = migrator._transform_message(msg, set())
    assert (
        transformed["post"]["message"]
        == "/me says _something italic_ to @abc with umläuts and **boldly emphasized** text"
    )


def test_preformatted_code(test_data_dir):
    """Test transformation of pre-formatted code blocks."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": [
            "Some multiline code snippet:\n\n",
            {"text": "foo\nbar\nfnord", "type": "pre"},
        ],
    }

    transformed = migrator._transform_message(msg, set())
    assert (
        transformed["post"]["message"]
        == "Some multiline code snippet:\n\n\n```\nfoo\nbar\nfnord\n```\n"
    )


def test_simple_chat_json(test_data_dir):
    """Test transformation of a simple chat JSON."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    chat_json = {
        "name": "telegram-to-mattermost Example Chat Group",
        "type": "private_supergroup",
        "id": 123456,
        "messages": [
            {
                "id": 12345678,
                "type": "message",
                "date": "2022-03-15T06:06:11",
                "from": "A. B. Cexample",
                "from_id": "user123",
                "text": "Morning!",
            },
            {
                "id": 12345679,
                "type": "message",
                "date": "2022-03-15T06:07:51",
                "from": "D. E. Fexample",
                "from_id": "user456",
                "text": "Mornin'!",
            },
        ],
    }

    messages = chat_json["messages"]
    replies = migrator._build_reply_structure(messages)
    output_lines = migrator._convert_messages(messages, replies)

    assert len(output_lines) == 3  # Version line + 2 messages
    assert output_lines[0] == '{"type":"version","version":1}'
    assert '"message": "Morning!"' in output_lines[1]
    assert '"message": "Mornin\'!"' in output_lines[2]


def test_reply_chain(test_data_dir):
    """Test handling of reply chains."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    messages = [
        {
            "id": 12345678,
            "type": "message",
            "date": "2022-03-15T06:06:11",
            "from": "A. B. Cexample",
            "from_id": "user123",
            "text": "Morning!",
        },
        {
            "id": 12345679,
            "type": "message",
            "date": "2022-03-15T06:07:51",
            "from": "D. E. Fexample",
            "from_id": "user456",
            "text": "Mornin'!",
            "reply_to_message_id": 12345678,
        },
    ]

    replies = migrator._build_reply_structure(messages)
    output_lines = migrator._convert_messages(messages, replies)

    assert len(output_lines) == 2  # Version line + 1 message with reply
    assert '"replies": [' in output_lines[1]
    assert '"user": "def"' in output_lines[1]


def test_nested_replies(test_data_dir):
    """Test handling of nested reply chains."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    messages = [
        {
            "id": 12345678,
            "type": "message",
            "date": "2022-03-15T06:06:11",
            "from": "A. B. Cexample",
            "from_id": "user123",
            "text": "Morning!",
        },
        {
            "id": 12345679,
            "type": "message",
            "date": "2022-03-15T06:07:51",
            "from": "D. E. Fexample",
            "from_id": "user456",
            "text": "Mornin'!",
            "reply_to_message_id": 12345678,
        },
        {
            "id": 12345680,
            "type": "message",
            "date": "2022-03-15T06:09:31",
            "from": "G. H. Ixample",
            "from_id": "user789",
            "text": "Good Morning!",
            "reply_to_message_id": 12345679,
        },
    ]

    replies = migrator._build_reply_structure(messages)
    output_lines = migrator._convert_messages(messages, replies)

    assert len(output_lines) == 2  # Version line + 1 message with nested replies
    assert '"replies": [' in output_lines[1]
    assert '"user": "def"' in output_lines[1]
    assert '"user": "ghi"' in output_lines[1]


def test_sticker_message(test_data_dir):
    """Test handling of sticker messages."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": "",
        "file": "stickers/sticker.webp",
        "thumbnail": "stickers/sticker.webp_thumb.jpg",
        "media_type": "sticker",
        "sticker_emoji": "🤦‍♂️",
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed["post"]["message"] == "🤦‍♂️"


def test_photo_attachment(test_data_dir):
    """Test handling of photo attachments."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))
    attachments = set()

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": "A photo",
        "photo": "photos/example-image.jpg",
        "width": 300,
        "height": 200,
    }

    transformed = migrator._transform_message(msg, attachments)
    assert "attachments" in transformed["post"]
    assert transformed["post"]["attachments"][0]["path"] == "photos/example-image.jpg"
    assert "photos/example-image.jpg" in attachments


def test_attachment_handling(test_data_dir):
    """Test handling of message attachments."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))
    attachments = set()

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": "A file",
        "file": "files/example-image.png",
        "thumbnail": "files/example-image.png_thumb.jpg",
        "mime_type": "image/png",
        "width": 300,
        "height": 200,
    }

    transformed = migrator._transform_message(msg, attachments)
    assert "attachments" in transformed["post"]
    assert transformed["post"]["attachments"][0]["path"] == "files/example-image.png"
    assert "files/example-image.png" in attachments


def test_invalid_config_file(tmp_path):
    """Test loading malformed or invalid YAML."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("invalid: yaml: :")

    with pytest.raises(yaml.YAMLError):
        TelegramMattermostMigrator(tmp_path, Path("output.zip"))


def test_invalid_timezone_config(tmp_path):
    """Test handling of invalid timezone."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
users:
  user123: abc
import_into:
  team: example
  channel: test
timezone: Invalid/Timezone
"""
    )

    with pytest.raises(ValueError, match="Invalid timezone"):
        TelegramMattermostMigrator(tmp_path, Path("output.zip"))


def test_valid_direct_chat_config(tmp_path):
    """Test valid direct chat configuration with minimal requirements."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
users:
  user123: abc
chat_type: direct_chat
"""
    )
    migrator = TelegramMattermostMigrator(tmp_path, Path("output.zip"))
    assert migrator.config.users == {"user123": "abc"}
    assert migrator.config.chat_type == "direct_chat"


def test_valid_channel_config(tmp_path):
    """Test valid channel configuration with all required fields."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
users:
  user123: abc
chat_type: channel
import_into:
  team: example
  channel: test
"""
    )
    migrator = TelegramMattermostMigrator(tmp_path, Path("output.zip"))
    assert migrator.config.chat_type == "channel"
    assert migrator.config.import_into == {"team": "example", "channel": "test"}


def test_config_missing_users(tmp_path):
    """Test configuration validation when users field is missing."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
chat_type: direct_chat
timezone: UTC
"""
    )
    with pytest.raises(KeyError, match="Missing required field 'users' in config"):
        TelegramMattermostMigrator(tmp_path, Path("output.zip"))


def test_channel_config_missing_import_into(tmp_path):
    """Test channel configuration validation when import_into is missing."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
users:
  user123: abc
chat_type: channel
"""
    )
    with pytest.raises(
        KeyError, match="Missing required field 'import_into' in config"
    ):
        TelegramMattermostMigrator(tmp_path, Path("output.zip"))


def test_sanitize_filename(test_data_dir):
    """Test filename sanitization method."""
    migrator = TelegramMattermostMigrator(Path(test_data_dir), Path("output.zip"))

    assert migrator._sanitize_filename("test.txt") == "test.txt"
    assert migrator._sanitize_filename("test space.txt") == "test_space.txt"
    assert migrator._sanitize_filename("test/path/file.txt") == "test/path/file.txt"
    assert (
        migrator._sanitize_filename("test$special#chars.txt")
        == "test_special_chars.txt"
    )


def test_create_zip_file_structure(tmp_path):
    """Test ZIP file creation and structure."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
users:
  user123: abc
import_into:
  team: example
  channel: test
"""
    )
    output_file = tmp_path / "test_output.zip"
    migrator = TelegramMattermostMigrator(Path(tmp_path), output_file)

    output_lines = ['{"type":"version","version":1}']
    migrator._create_zip_file(output_lines)

    with zipfile.ZipFile(output_file) as zf:
        files = zf.namelist()
        assert "data/photos/" in files
        assert "data/files/" in files
        assert "data/video_files/" in files
        assert "data/voice_messages/" in files
        assert "import.jsonl" in files


def test_missing_attachments_handling(test_data_dir):
    """Test handling of missing attachment files."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": "Missing file",
        "file": "files/nonexistent.png",
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed is not None
    assert "attachments" in transformed["post"]


def test_unknown_message_type_handling(test_data_dir):
    """Test handling of unknown message types."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "unknown_type",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": "Test",
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed is None


def test_malformed_message_handling(test_data_dir):
    """Test handling of messages missing required fields."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "message",
        # Missing date
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": "Test",
    }

    with pytest.raises(KeyError):
        migrator._transform_message(msg, set())


def test_unsupported_text_element_handling(test_data_dir):
    """Test handling of unsupported text formatting."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "A. B. Cexample",
        "from_id": "user123",
        "text": [{"type": "unknown_format", "text": "Test"}],
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed["post"]["message"] == ""


def test_invalid_user_id_handling(test_data_dir):
    """Test handling of messages with unknown user IDs."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        "id": 123456,
        "type": "message",
        "date": "2022-03-15T06:06:11",
        "from": "Unknown User",
        "from_id": "unknown_user",
        "text": "Test",
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed is None


def test_conversation_log_basic(test_data_dir, tmp_path):
    """Test basic conversation log creation with simple messages."""
    log_file = tmp_path / "conversation.log"
    migrator = TelegramMattermostMigrator(
        test_data_dir, Path("output.zip"), conversation_log=log_file
    )

    messages = [
        {
            "id": 1,
            "type": "message",
            "date": "2022-03-15T06:06:11",
            "from": "A. B. Cexample",
            "from_id": "user123",
            "text": "Hello world",
        }
    ]

    migrator._write_conversation_log(messages, {})
    assert log_file.exists()
    content = log_file.read_text()
    assert "CONVERSATION LOG LEGEND" in content
    assert "[2022-03-15 06:06:11] @abc:" in content
    assert "Hello world" in content


def test_conversation_log_replies(test_data_dir, tmp_path):
    """Test conversation log with reply chains."""
    log_file = tmp_path / "conversation.log"
    migrator = TelegramMattermostMigrator(
        test_data_dir, Path("output.zip"), conversation_log=log_file
    )

    messages = [
        {
            "id": 1,
            "type": "message",
            "date": "2022-03-15T06:06:11",
            "from": "A. B. Cexample",
            "from_id": "user123",
            "text": "First message",
        },
        {
            "id": 2,
            "type": "message",
            "date": "2022-03-15T06:07:11",
            "from": "D. E. Fexample",
            "from_id": "user456",
            "text": "Reply to first",
            "reply_to_message_id": 1,
        },
    ]

    migrator._write_conversation_log(messages, {})
    content = log_file.read_text()
    assert "> @def: First message" in content


def test_conversation_log_attachments(test_data_dir, tmp_path):
    """Test conversation log with various attachment types."""
    log_file = tmp_path / "conversation.log"
    migrator = TelegramMattermostMigrator(
        test_data_dir, Path("output.zip"), conversation_log=log_file
    )

    messages = [
        {
            "id": 1,
            "type": "message",
            "date": "2022-03-15T06:06:11",
            "from": "A. B. Cexample",
            "from_id": "user123",
            "text": "Check these out",
            "photo": "photos/image.jpg",
        },
        {
            "id": 2,
            "type": "message",
            "date": "2022-03-15T06:07:11",
            "from": "D. E. Fexample",
            "from_id": "user456",
            "text": "And this",
            "file": "files/document.pdf",
        },
    ]

    migrator._write_conversation_log(messages, {})
    content = log_file.read_text()
    assert "[PHOTO: image.jpg]" in content
    assert "[FILE: document.pdf]" in content


def test_conversation_log_formatting(test_data_dir, tmp_path):
    """Test conversation log with complex message formatting."""
    log_file = tmp_path / "conversation.log"
    migrator = TelegramMattermostMigrator(
        test_data_dir, Path("output.zip"), conversation_log=log_file
    )

    messages = [
        {
            "id": 1,
            "type": "message",
            "date": "2022-03-15T06:06:11",
            "from": "A. B. Cexample",
            "from_id": "user123",
            "text": [
                "Hello ",
                {"text": "world", "type": "bold"},
                " with ",
                {"text": "formatting", "type": "italic"},
            ],
        }
    ]

    migrator._write_conversation_log(messages, {})
    content = log_file.read_text()
    assert "Hello **world** with _formatting_" in content


def test_validate_input_dir(tmp_path):
    """Test input directory validation."""
    with pytest.raises(ValueError, match="Input directory does not exist"):
        validate_input_dir(tmp_path / "nonexistent")

    # Create empty directory
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()

    with pytest.raises(ValueError, match="Config file not found"):
        validate_input_dir(test_dir)


def test_invalid_telegram_export(tmp_path):
    """Test handling of invalid result.json."""
    test_dir = tmp_path / "test_dir"
    test_dir.mkdir()

    # Create config file
    config_file = test_dir / "config.yaml"
    config_file.write_text("users: {}")

    # Create invalid result.json
    result_file = test_dir / "result.json"
    result_file.write_text("{invalid json")

    with pytest.raises(ValueError, match="result.json is not valid JSON"):
        validate_input_dir(test_dir)


def test_circular_reply_handling(test_data_dir):
    """Test handling of circular reply chains."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    messages = [
        {
            "id": 1,
            "type": "message",
            "date": "2022-03-15T06:06:11",
            "from": "A. B. Cexample",
            "from_id": "user123",
            "text": "First",
            "reply_to_message_id": 2,
        },
        {
            "id": 2,
            "type": "message",
            "date": "2022-03-15T06:07:11",
            "from": "D. E. Fexample",
            "from_id": "user456",
            "text": "Second",
            "reply_to_message_id": 1,
        },
    ]

    replies = migrator._build_reply_structure(messages)
    assert len(replies) > 0  # Should handle circular references without infinite loop


def test_broken_reply_chain(test_data_dir):
    """Test handling of replies to non-existent messages."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    messages = [
        {
            "id": 1,
            "type": "message",
            "date": "2022-03-15T06:06:11",
            "from": "A. B. Cexample",
            "from_id": "user123",
            "text": "Reply to missing message",
            "reply_to_message_id": 999,  # Non-existent message
        }
    ]

    replies = migrator._build_reply_structure(messages)
    assert 999 not in replies  # Should handle missing message gracefully
