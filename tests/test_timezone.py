"""Tests for timezone handling in telegram-to-mattermost converter."""

from pathlib import Path
from telegram_to_mattermost.migrate import TelegramMattermostMigrator

def test_config_loading_with_timezone(test_data_dir):
    """Test loading configuration with timezone specification."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"), "config_tz.yaml")
    assert migrator.config.timezone == "Europe/Busingen"

def test_date_conversion_with_timezone(test_data_dir):
    """Test date conversion with configured timezone."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"), "config_tz.yaml")
    # Test specific timestamp conversion with timezone
    assert migrator._date_to_epoch("2022-03-25T17:30:36") == 1648225836000

def test_message_transformation_with_timezone(test_data_dir):
    """Test message transformation with timezone configuration."""
    migrator = TelegramMattermostMigrator(test_data_dir, Path("output.zip"), "config_tz.yaml")

    msg = {
        'id': 123456,
        'type': 'message',
        'date': '2022-03-15T06:06:11',
        'from': 'A. B. Cexample',
        'from_id': 'user123',
        'text': 'Morning!'
    }

    transformed = migrator._transform_message(msg, set())
    assert transformed['post']['create_at'] == 1647320771000
