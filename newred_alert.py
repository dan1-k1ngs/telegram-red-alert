import asyncio
import re
from telethon import TelegramClient, events

api_id = 35624393
api_hash = "0405f4fc5638f029319e213b13974776"

# ID o username del grupo origen
source_group = -1002947363037

# Destino de la alerta:
# "me" = Mensajes guardados
# o pon el username/ID de otro chat tuyo
target_chat = -1002847668460

keyword = "RED"
whole_word_only = True

client = TelegramClient("red_alert_session", api_id, api_hash)


def contains_keyword(text: str, keyword: str) -> bool:
    if not text:
        return False

    if whole_word_only:
        pattern = rf"\b{re.escape(keyword)}\b"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None

    return keyword.lower() in text.lower()


@client.on(events.NewMessage(chats=source_group))
async def handler(event):
    text = event.raw_text or ""

    if not contains_keyword(text, keyword):
        return

    sender = await event.get_sender()
    chat = await event.get_chat()

    sender_name = getattr(sender, "first_name", None) or getattr(sender, "username", None) or "Desconocido"
    chat_name = getattr(chat, "title", None) or "Grupo"

    alert = (
        f"🚨 RED detectado\n\n"
        f"Grupo: {chat_name}\n"
        f"Usuario: {sender_name}\n"
        f"Mensaje: {text}"
    )

    await client.send_message(target_chat, alert)
    print("Alerta enviada:", alert)


async def main():
    await client.start()
    print("Escuchando mensajes...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())