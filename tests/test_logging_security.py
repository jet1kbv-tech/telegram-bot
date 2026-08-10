import logging

from bot.logging_config import SecretRedactionFilter, configure_logging


def test_sensitive_http_values_are_redacted_from_emitted_logs(caplog):
    configure_logging()
    logger = logging.getLogger("security-regression")
    logger.addFilter(SecretRedactionFilter())
    telegram_token = "123456:telegram-secret"
    provider_key = "polza-secret"
    with caplog.at_level(logging.INFO):
        logger.info("POST https://api.telegram.org/bot%s/getUpdates Authorization: Bearer %s", telegram_token, provider_key)
    assert telegram_token not in caplog.text
    assert provider_key not in caplog.text
    assert "Authorization: <redacted>" in caplog.text
