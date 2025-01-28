import os
import asyncio
import json
from telethon import TelegramClient, errors
from telegram import Bot
from dotenv import load_dotenv
import logging

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Получение переменных из .env
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_IDS_RAW = os.getenv('CHAT_IDS')  # Строка с ID чатов
TARGET_REACH = os.getenv('TARGET_REACH', '300')  # Пороговое значение охвата
PHONE_NUMBER = os.getenv('PHONE_NUMBER')  # Ваш номер телефона
CHANNEL_NAMES_RAW = os.getenv('CHANNEL_NAMES')  # JSON-массив с названиями каналов

# Проверка обязательных переменных
required_vars = {
    'API_ID': API_ID,
    'API_HASH': API_HASH,
    'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
    'CHAT_IDS': CHAT_IDS_RAW,
    'CHANNEL_NAMES': CHANNEL_NAMES_RAW,
    'PHONE_NUMBER': PHONE_NUMBER
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    logger.error(f'Отсутствуют обязательные переменные окружения: {", ".join(missing_vars)}')
    exit(1)

# Парсинг CHAT_IDS
try:
    CHAT_IDS = [int(chat_id.strip()) for chat_id in CHAT_IDS_RAW.split(',') if chat_id.strip().isdigit()]
except ValueError as ve:
    logger.error(f'Ошибка при парсинге CHAT_IDS: {ve}')
    exit(1)

if not CHAT_IDS:
    logger.error('Не найдено валидных CHAT_IDS.')
    exit(1)

# Парсинг CHANNEL_NAMES как JSON-массив
try:
    CHANNEL_NAMES = json.loads(CHANNEL_NAMES_RAW)
    logger.info(f'CHANNEL_NAMES: {CHANNEL_NAMES}')
except json.JSONDecodeError as je:
    logger.error(f'Ошибка при разборе CHANNEL_NAMES как JSON: {je}')
    exit(1)

if not CHANNEL_NAMES:
    logger.error('Нет каналов для отслеживания. Проверьте переменную CHANNEL_NAMES.')
    exit(1)

# Конвертация TARGET_REACH в целое число
try:
    TARGET_REACH = int(TARGET_REACH)
except ValueError:
    logger.error('Переменная TARGET_REACH должна быть целым числом.')
    exit(1)

# Инициализация клиентов
client = TelegramClient('session_name', API_ID, API_HASH)
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Файл для хранения уведомленных сообщений
NOTIFIED_FILE = 'notified_messages.json'

# Загрузка уже уведомленных сообщений из файла
if os.path.exists(NOTIFIED_FILE):
    with open(NOTIFIED_FILE, 'r', encoding='utf-8') as f:
        try:
            notified_messages = json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f'Ошибка при чтении {NOTIFIED_FILE}: {e}')
            notified_messages = {channel: [] for channel in CHANNEL_NAMES}
else:
    notified_messages = {channel: [] for channel in CHANNEL_NAMES}

def save_notified_messages():
    with open(NOTIFIED_FILE, 'w', encoding='utf-8') as f:
        json.dump(notified_messages, f, ensure_ascii=False, indent=4)

async def send_notification(channel_title, message):
    # Ограничим длину текста поста до 100 символов
    post_text = (message.text[:100] + '...') if message.text and len(message.text) > 100 else (message.text or 'Без текста')
    text = (f'📈 **Новый достигнутый охват!**\n'
            f'**Канал:** {channel_title}\n'
            f'**Пост:** {post_text}\n'
            f'**Просмотров:** {message.views}')
    
    tasks = []
    for chat_id in CHAT_IDS:
        tasks.append(
            bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for chat_id, result in zip(CHAT_IDS, results):
        if isinstance(result, Exception):
            logger.error(f'Ошибка при отправке уведомления в чат {chat_id}: {result}')
        else:
            logger.info(f'Отправлено уведомление в чат {chat_id} для поста {message.id} в канале "{channel_title}".')

async def get_channel_entity(channel_name):
    try:
        dialogs = await client.get_dialogs()
        for dialog in dialogs:
            if dialog.is_channel and dialog.title == channel_name:
                logger.info(f'Найден канал: {dialog.title}')
                return dialog.entity
        logger.warning(f'Канал с названием "{channel_name}" не найден.')
        return None
    except Exception as e:
        logger.error(f'Ошибка при получении сущности канала "{channel_name}": {e}')
        return None

async def monitor_channel(channel_name, channel_entity):
    try:
        async for message in client.iter_messages(channel_entity, limit=100):
            if message.id in notified_messages.get(channel_name, []):
                continue
            if message.views and message.views >= TARGET_REACH:
                await send_notification(channel_entity.title, message)
                notified_messages[channel_name].append(message.id)
                save_notified_messages()
    except errors.rpcerrorlist.ChannelPrivateError:
        logger.error(f'Доступ к приватному каналу "{channel_name}" запрещён.')
    except Exception as e:
        logger.error(f'Ошибка при мониторинге канала "{channel_name}": {e}')

async def monitor_all_channels(channel_entities):
    tasks = []
    for channel_name, channel_entity in channel_entities.items():
        if channel_entity:
            tasks.append(monitor_channel(channel_name, channel_entity))
    if tasks:
        await asyncio.gather(*tasks)

async def main():
    await client.start(phone=PHONE_NUMBER)
    logger.info('Клиент Telethon запущен и авторизован.')

    # Получение сущностей всех каналов
    channel_entities = {}
    for channel_name in CHANNEL_NAMES:
        entity = await get_channel_entity(channel_name)
        channel_entities[channel_name] = entity

    if not any(channel_entities.values()):
        logger.error('Не удалось найти ни одного указанного канала. Завершение работы.')
        return

    try:
        while True:
            await monitor_all_channels(channel_entities)
            logger.info('Проверка выполнена. Ожидание 1 минуты.')
            await asyncio.sleep(60)  # Проверять каждую минуту
    except KeyboardInterrupt:
        logger.info('Программа остановлена пользователем.')
    finally:
        await client.disconnect()
        logger.info('Клиент Telethon отключён.')

if __name__ == '__main__':
    asyncio.run(main())
