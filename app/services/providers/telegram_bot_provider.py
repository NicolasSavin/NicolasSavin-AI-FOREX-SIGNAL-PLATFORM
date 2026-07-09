import os
from app.services.media_import_engine import EmptyProvider
class TelegramBotProvider(EmptyProvider):
    def __init__(self):
        super().__init__('telegram_bot' if os.getenv('TELEGRAM_BOT_TOKEN') else 'telegram_bot_placeholder')
