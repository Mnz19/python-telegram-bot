import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from telegram.ext import Application, CommandHandler, ConversationHandler

from alerts.bot.commands.alerts import current_price, list_alerts, stop_alert
from alerts.bot.commands.start import start
from alerts.bot.conversation import build_conversation_states, cancel, start_new_alert

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Runs the Telegram bot using long polling."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError("TELEGRAM_BOT_TOKEN não configurado. Defina no .env.")

        application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

        conversation_handler = ConversationHandler(
            entry_points=[CommandHandler("novaalerta", start_new_alert)],
            states=build_conversation_states(),
            fallbacks=[CommandHandler("cancelar", cancel)],
        )

        application.add_handler(CommandHandler("start", start))
        application.add_handler(conversation_handler)
        application.add_handler(CommandHandler("listaralertas", list_alerts))
        application.add_handler(CommandHandler("pararalerta", stop_alert))
        application.add_handler(CommandHandler("precoatual", current_price))

        self.stdout.write(self.style.SUCCESS("Bot iniciado, aguardando mensagens (polling)..."))
        application.run_polling(allowed_updates=None)
