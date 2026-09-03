import unittest
from unittest.mock import MagicMock, patch

import bot


class TelegramProxyTests(unittest.TestCase):
    @patch("bot.Application.builder")
    def test_build_application_without_proxy(self, builder_factory):
        builder = MagicMock()
        builder_factory.return_value = builder
        builder.token.return_value = builder
        builder.build.return_value = object()

        with patch.object(bot, "TELEGRAM_PROXY_URL", None):
            bot.build_application()

        builder.proxy.assert_not_called()
        builder.get_updates_proxy.assert_not_called()
        builder.build.assert_called_once_with()

    @patch("bot.Application.builder")
    def test_build_application_with_proxy(self, builder_factory):
        builder = MagicMock()
        builder_factory.return_value = builder
        builder.token.return_value = builder
        builder.proxy.return_value = builder
        builder.get_updates_proxy.return_value = builder
        builder.build.return_value = object()

        proxy = "http://proxy.example:8080"
        with patch.object(bot, "TELEGRAM_PROXY_URL", proxy):
            bot.build_application()

        builder.proxy.assert_called_once_with(proxy)
        builder.get_updates_proxy.assert_called_once_with(proxy)
        builder.build.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
