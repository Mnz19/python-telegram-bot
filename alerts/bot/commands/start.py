from telegram import Update
from telegram.ext import ContextTypes

WELCOME_MESSAGE = (
    "Olá! Eu monitoro preços de passagens aéreas e te aviso quando encontrar uma queda, "
    "um novo menor preço histórico, ou um possível erro de tarifa.\n\n"
    "Comandos disponíveis:\n"
    "/novaalerta - criar um novo alerta de preço\n"
    "/listaralertas - ver seus alertas\n"
    "/pararalerta <id> ou /pausar <id> - pausar um alerta\n"
    "/retomar <id> - reativar um alerta pausado\n"
    "/precoatual <id> - forçar uma checagem de preço agora\n"
    "/perfis - ver seus alertas nomeados (reutilizáveis)\n"
    "/duplicar <id> - criar um novo alerta a partir de um existente\n\n"
    "Também funciono em grupos: crie um alerta no grupo e todo mundo lá "
    "recebe o aviso e pode gerenciá-lo."
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MESSAGE)
