"""Basic functionality tests for telegram-to-mattermost converter."""

import pytest
from pathlib import Path
import yaml
import zipfile
from telegram_to_mattermost.migrate import TelegramMattermostMigrator, validate_input_dir

def test_config_loading(mock_config, test_data_dir):
    """Test loading the example configuration."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))
    assert migrator.config.users == {
        'user123': 'abc',
        'user789': 'ghi',
        'user456': 'def'
    }
    assert migrator.config.import_into == {
        'team': 'example',
        'channel': 'town square'
    }

def test_date_conversion(mock_config, test_data_dir):
    """Test date to epoch conversion."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))
    assert migrator._date_to_epoch("2022-03-25T17:30:36") == 1648229436000

def test_message_transformation(mock_config, test_data_dir):
    """Test basic message transformation."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': 'Morning!'
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed == {
        'type': 'post',
        'id': 123456,
        'post': {
            'team': 'example',
            'channel': 'town square',
            'user': 'abc',
            'message': 'Morning!',
            'create_at': 1647324371000,
            'edit_at': 0
        }
    }

def test_complex_message_transformation(mock_config, test_data_dir):
    """Test transformation of messages with complex formatting."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': [
            {'text': '/me', 'type': 'bot_command'},
            ' says ',
            {'text': 'something italic', 'type': 'italic'},
            ' to ',
            {'text': 'Anna', 'user_id': 123, 'type': 'mention_name'},
            ' with umläuts and ',
            {'text': 'boldly emphasized', 'type': 'bold'},
            ' text'
        ]
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed['post']['message'] == '/me says _something italic_ to @abc with umläuts and **boldly emphasized** text'

def test_preformatted_code(mock_config, test_data_dir):
    """Test transformation of pre-formatted code blocks."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': [
            "Some multiline code snippet:\n\n",
            {
                'text': "foo\nbar\nfnord",
                'type': 'pre'
            }
        ]
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed['post']['message'] == "Some multiline code snippet:\n\n\n```\nfoo\nbar\nfnord\n```\n"

def test_simple_chat_json(mock_config, test_data_dir):
    """Test transformation of a simple chat JSON."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    chat_json = {
        'name': 'telegram2mm Example Chat Group',
        'type': 'private_supergroup',
        'id': 123456,
        'messages': [
            {
                'id': 12345678,
                'type': 'message',
                'date': '2022-03-15T06:06:11',
                'from': 'A. B. Cexample',
                'from_id': 'user123',
                'text': 'Morning!'
            },
            {
                'id': 12345679,
                'type': 'message',
                'date': '2022-03-15T06:07:51',
                'from': 'D. E. Fexample',
                'from_id': 'user456',
                'text': "Mornin'!"
            }
        ]
    }

    messages = chat_json['messages']
    replies = migrator._build_reply_structure(messages)
    output_lines = migrator._convert_messages(messages, replies)

    assert len(output_lines) == 3  # Version line + 2 messages
    assert output_lines[0] == '{"type":"version","version":1}'
    assert '"message":"Morning!"' in output_lines[1]
    assert '"message":"Mornin\'!"' in output_lines[2]

def test_reply_chain(mock_config, test_data_dir):
    """Test handling of reply chains."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    messages = [
        {
            'id': 12345678,
            'type': 'message',
            'date': '2022-03-15T06:06:11',
            'from': 'A. B. Cexample',
            'from_id': 'user123',
            'text': 'Morning!'
        },
        {
            'id': 12345679,
            'type': 'message',
            'date': '2022-03-15T06:07:51',
            'from': 'D. E. Fexample',
            'from_id': 'user456',
            'text': "Mornin'!",
            'reply_to_message_id': 12345678
        }
    ]

    replies = migrator._build_reply_structure(messages)
    output_lines = migrator._convert_messages(messages, replies)

    assert len(output_lines) == 2  # Version line + 1 message with reply
    assert '"replies":[' in output_lines[1]
    assert '"user":"def"' in output_lines[1]

def test_nested_replies(mock_config, test_data_dir):
    """Test handling of nested reply chains."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    messages = [
        {
            'id': 12345678,
            'type': 'message',
            'date': '2022-03-15T06:06:11',
            'from': 'A. B. Cexample',
            'from_id': 'user123',
            'text': 'Morning!'
        },
        {
            'id': 12345679,
            'type': 'message',
            'date': '2022-03-15T06:07:51',
            'from': 'D. E. Fexample',
            'from_id': 'user456',
            'text': "Mornin'!",
            'reply_to_message_id': 12345678
        },
        {
            'id': 12345680,
            'type': 'message',
            'date': '2022-03-15T06:09:31',
            'from': 'G. H. Ixample',
            'from_id': 'user789',
            'text': 'Good Morning!',
            'reply_to_message_id': 12345679
        }
    ]

    replies = migrator._build_reply_structure(messages)
    output_lines = migrator._convert_messages(messages, replies)

    assert len(output_lines) == 2  # Version line + 1 message with nested replies
    assert '"replies":[' in output_lines[1]
    assert '"user":"def"' in output_lines[1]
    assert '"user":"ghi"' in output_lines[1]

def test_sticker_message(mock_config, test_data_dir):
    """Test handling of sticker messages."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': '',
        'file': 'stickers/sticker.webp',
        'thumbnail': 'stickers/sticker.webp_thumb.jpg',
        'media_type': 'sticker',
        'sticker_emoji': '🤦‍♂️'
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed['post']['message'] == '🤦‍♂️'

def test_photo_attachment(mock_config, test_data_dir):
    """Test handling of photo attachments."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))
    attachments = set()

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': 'A photo',
        'photo': 'photos/example-image.jpg',
        'width': 300,
        'height': 200
    }

    transformed = migrator._transform_message(msg, attachments)
    assert 'attachments' in transformed['post']
    assert transformed['post']['attachments'][0]['path'] == 'photos/example-image.jpg'
    assert 'photos/example-image.jpg' in attachments

def test_attachment_handling(mock_config, test_data_dir):
    """Test handling of message attachments."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))
    attachments = set()

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': 'A file',
        'file': 'files/example-image.png',
        'thumbnail': 'files/example-image.png_thumb.jpg',
        'mime_type': 'image/png',
        'width': 300,
        'height': 200
    }

    transformed = migrator._transform_message(msg, attachments)
    assert 'attachments' in transformed['post']
    assert transformed['post']['attachments'][0]['path'] == 'files/example-image.png'
    assert 'files/example-image.png' in attachments

def test_invalid_config_file(tmp_path):
    """Test loading malformed or invalid YAML."""
    config_file = tmp_path / "invalid_config.yml"
    config_file.write_text("invalid: yaml: :")

    with pytest.raises(yaml.YAMLError):
        TelegramMattermostMigrator(tmp_path, Path("output.zip"))

def test_invalid_timezone_config(tmp_path):
    """Test handling of invalid timezone."""
    config_file = tmp_path / "config.yml"
    config_file.write_text("""
users:
  user123: abc
import_into:
  team: example
  channel: test
timezone: Invalid/Timezone
""")

    with pytest.raises(ValueError, match="Invalid timezone"):
        TelegramMattermostMigrator(tmp_path, Path("output.zip"))

def test_missing_required_config_fields(tmp_path):
    """Test missing team/channel/users configuration."""
    config_file = tmp_path / "config.yml"
    config_file.write_text("timezone: UTC")

    with pytest.raises(KeyError):
        TelegramMattermostMigrator(tmp_path, Path("output.zip"))

def test_sanitize_filename():
    """Test filename sanitization method."""
    migrator = TelegramMattermostMigrator(Path("."), Path("output.zip"))

    assert migrator._sanitize_filename("test.txt") == "test.txt"
    assert migrator._sanitize_filename("test space.txt") == "test_space.txt"
    assert migrator._sanitize_filename("test/path/file.txt") == "test/path/file.txt"
    assert migrator._sanitize_filename("test$special#chars.txt") == "test_special_chars.txt"

def test_create_zip_file_structure(tmp_path):
    """Test ZIP file creation and structure."""
    output_file = tmp_path / "test_output.zip"
    migrator = TelegramMattermostMigrator(Path("."), output_file)

    output_lines = ['{"type":"version","version":1}']
    migrator._create_zip_file(output_lines)

    with zipfile.ZipFile(output_file) as zf:
        files = zf.namelist()
        assert "data/photos/" in files
        assert "data/files/" in files
        assert "data/video_files/" in files
        assert "data/voice_messages/" in files
        assert "import.jsonl" in files

def test_missing_attachments_handling(tmp_path, mock_config):
    """Test handling of missing attachment files."""
    migrator = TelegramMattermostMigrator(tmp_path, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': 'Missing file',
        'file': 'files/nonexistent.png'
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed is not None
    assert 'attachments' in transformed['post']

def test_unknown_message_type_handling(mock_config, test_data_dir):
    """Test handling of unknown message types."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'unknown_type',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': 'Test'
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed is None

def test_malformed_message_handling(mock_config, test_data_dir):
    """Test handling of messages missing required fields."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'message',
        # Missing date
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': 'Test'
    }

    with pytest.raises(KeyError):
        migrator._transform_message(msg, set())

def test_unsupported_text_element_handling(mock_config, test_data_dir):
    """Test handling of unsupported text formatting."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': [
            {'type': 'unknown_format', 'text': 'Test'}
        ]
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed['post']['message'] == ''

def test_invalid_user_id_handling(mock_config, test_data_dir):
    """Test handling of messages with unknown user IDs."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'Unknown User',
        'from_id': 'unknown_user',
        'text': 'Test'
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed is None

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

def test_circular_reply_handling(mock_config, test_data_dir):
    """Test handling of circular reply chains."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    messages = [
        {
            'id': 1,
            'type': 'message',
            'date': '2022-03-15T06:06:11',
            'from': 'A. B. Cexample',
            'from_id': 'user123',
            'text': 'First',
            'reply_to_message_id': 2
        },
        {
            'id': 2,
            'type': 'message',
            'date': '2022-03-15T06:07:11',
            'from': 'D. E. Fexample',
            'from_id': 'user456',
            'text': 'Second',
            'reply_to_message_id': 1
        }
    ]

    replies = migrator._build_reply_structure(messages)
    assert len(replies) > 0  # Should handle circular references without infinite loop

def test_broken_reply_chain(mock_config, test_data_dir):
    """Test handling of replies to non-existent messages."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"))

    messages = [
        {
            'id': 1,
            'type': 'message',
            'date': '2022-03-15T06:06:11',
            'from': 'A. B. Cexample',
            'from_id': 'user123',
            'text': 'Reply to missing message',
            'reply_to_message_id': 999  # Non-existent message
        }
    ]

    replies = migrator._build_reply_structure(messages)
    assert 999 not in replies  # Should handle missing message gracefully
