from telegram import Update
from telegram.ext import ContextTypes

WELCOME_MESSAGE = (
    "Olá! Eu monitoro preços de passagens aéreas e te aviso quando encontrar uma queda.\n\n"
    "Comandos disponíveis:\n"
    "/novaalerta - criar um novo alerta de preço\n"
    "/listaralertas - ver seus alertas ativos\n"
    "/pararalerta <id> - desativar um alerta\n"
    "/precoatual <id> - forçar uma checagem de preço agora\n"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MESSAGE)
