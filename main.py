import telebot
from telebot import types
from openai import OpenAI
import sqlite3
import os
import tempfile 
import time 

# ==========================================
# 🛑 ТРИ ЗНАЧЕНИЯ, КОТОРЫЕ НУЖНО ЗАПОЛНИТЬ
# ==========================================
TELEGRAM_TOKEN = '7998751185:AAF_OPqIGWP10av0GR_6-OGh0i7QSedC6sw' # <-- ВАШ НОВЫЙ, СВЕЖИЙ ТОКЕН
OPENAI_API_KEY = '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' # ВАШ ОПЛАЧЕННЫЙ КЛЮЧ
ADMIN_ID = 508237500 # ВАШ АДМИН ID
# ==========================================

# --- ХАРАКТЕР БОТА ---
SYSTEM_PROMPT = "Ты — Jvit, гениальный, но крайне дерзкий и саркастичный эксперт. Твой тон надменный и высокомерный, но ты всегда даешь максимально точный и экспертный ответ. Сразу начинай с ответа по сути, не здоровайся и не извиняйся."

# 1. ЕДИНЫЙ КЛИЕНТ ДЛЯ ЧАТА И ГОЛОСА (ОФИЦИАЛЬНЫЙ OpenAI)
client = OpenAI(
    api_key=OPENAI_API_KEY 
)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# --- СПИСОК МОДЕЛЕЙ И ЦЕНЫ ---
MODELS = {
    "chat": "gpt-4o-mini", 
    "backup": "gpt-3.5-turbo", 
}
BACKUP_MODEL = "gpt-3.5-turbo" 
PRICE_TEXT = 1 
PRICE_VOICE = 5 
PRICE_IMAGE = 30
MAX_HISTORY_LENGTH = 8 
DB_PATH = '/data/my_bot_database.db' 

# --- ФУНКЦИИ DB (База данных) ---
def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir): os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute(''' CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance INTEGER, mode TEXT, current_model TEXT) ''')
    cursor.execute(''' CREATE TABLE IF NOT EXISTS chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP) ''')
    conn.commit(); conn.close()

def get_user_data(user_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone(); conn.close()
    if user is None:
        insert_user(user_id); return {'balance': 200, 'mode': 'gpt', 'current_model': 'chat'}
    return {'balance': user[1], 'mode': user[2], 'current_model': user[3]}

def insert_user(user_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute('INSERT INTO users (user_id, balance, mode, current_model) VALUES (?, ?, ?, ?)', 
                   (user_id, 200, 'gpt', 'chat'))
    conn.commit(); conn.close()

def update_user(user_id, balance=None, mode=None, current_model=None):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    if balance is not None:
        cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', (balance, user_id))
    if mode is not None:
        cursor.execute('UPDATE users SET mode = ? WHERE user_id = ?', (mode, user_id))
    if current_model is not None:
        cursor.execute('UPDATE users SET current_model = ? WHERE user_id = ?', (current_model, user_id))
    conn.commit(); conn.close()

def save_history(user_id, role, content):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute('INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)', (user_id, role, content))
    conn.commit()
    cursor.execute('SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?', (user_id, MAX_HISTORY_LENGTH))
    history_ids = [row[0] for row in cursor.fetchall()]
    if len(history_ids) == MAX_HISTORY_LENGTH:
        cursor.execute('DELETE FROM chat_history WHERE user_id = ? AND id NOT IN ({})'.format(','.join(['?'] * len(history_ids))), tuple([user_id] + history_ids))
        conn.commit()
    conn.close()

def load_history(user_id):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute('SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id ASC', (user_id,))
    history = [{'role': role, 'content': content} for role, content in cursor.fetchall()]
    conn.close()
    return history

init_db() 

# --- ФУНКЦИЯ ОЧИСТКИ ТЕКСТА ---
def clean_text(text):
    if not text: return ""
    text = text.replace("<s>", "").replace("</s>", "")
    text = text.replace("[INST]", "").replace("[/INST]", "")
    text = text.replace("[OUT]", "").replace("[/OUT]", "")
    return text.strip()

# --- АВТОМАТИЧЕСКАЯ ФУНКЦИЯ ЧАТА (С ПАМЯТЬЮ) ---
def process_llm_response(message, user, prompt_text, cost):
    user_id = message.from_user.id
    model_name = user['current_model']
    primary_model_id = MODELS.get(model_name, MODELS["chat"]) 
    
    history = load_history(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": prompt_text})
    
    try:
        response = client.chat.completions.create(
            model=primary_model_id, 
            messages=messages, 
        )
        ai_text = response.choices[0].message.content
        final_text = clean_text(ai_text)
        
        if not final_text: raise Exception("Пустой ответ")
        
        bot.reply_to(message, final_text)
        # Списываем токены
        update_user(user_id, balance=user['balance'] - cost)
        
        # Сохраняем историю
        save_history(user_id, 'user', prompt_text)
        save_history(user_id, 'assistant', final_text)

    except Exception as e:
        print(f"СБОЙ ОСНОВНОЙ ({primary_model_id}): {e}")
        
        # Резервный механизм
        if primary_model_id == BACKUP_MODEL:
            bot.reply_to(message, "❌ API OpenAI временно недоступен.")
            return

        bot.send_message(message.chat.id, f"⚠️ Основная сеть занята. Подключаю резерв...")
        try:
            backup_response = client.chat.completions.create(
                model=BACKUP_MODEL, 
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt_text}],
            )
            backup_text = clean_text(backup_response.choices[0].message.content)
            bot.reply_to(message, f"Резервный ответ:\n{backup_text}")
            update_user(user_id, balance=user['balance'] - cost)
            save_history(user_id, 'user', prompt_text)
            save_history(user_id, 'assistant', backup_text)
        except Exception as backup_e:
            print(f"СБОЙ РЕЗЕРВА ({BACKUP_MODEL}): {backup_e}")
            bot.reply_to(message, f"❌ Все каналы заняты. Попробуй позже.")

# --- ОБРАБОТЧИК ДЛЯ ГОЛОСА ---
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    user_id = message.from_user.id
    user = get_user_data(user_id)
    
    if user['balance'] < PRICE_VOICE:
        bot.reply_to(message, f"❌ Мало токенов! Для голоса нужно {PRICE_VOICE}.")
        return

    bot.send_chat_action(message.chat.id, 'record_audio')
    bot.send_message(message.chat.id, "🎙️ Анализирую голос, подожди...")

    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_audio:
            temp_audio.write(downloaded_file)
            temp_file_name = temp_audio.name
            temp_audio.close()

        with open(temp_file_name, "rb") as audio_file:
            transcript = client.audio.transcriptions.create( 
                model="whisper-1", 
                file=audio_file,
            )
        
        os.remove(temp_file_name)

        prompt_text = transcript.text
        bot.send_message(message.chat.id, f"✅ Распознано: «{prompt_text}»\n\n🧠 Генерирую ответ...")
        process_llm_response(message, user, prompt_text, PRICE_VOICE)

    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка транскрибации или API: {e}")
        print(f"WHISPER ERROR: {e}") 

# --- ОБРАБОТЧИК ТЕКСТА ---
@bot.message_handler(content_types=['text'])
def handle_text_message(message):
    user = get_user_data(message.from_user.id)
    
    if message.text.startswith('/'): return

    MENU_BUTTONS = ["🤖 Чат с AI", "🎨 Рисование", "👤 Баланс", "⚙️ Настройки"]
    if message.text in MENU_BUTTONS: return 

    # 1. РИСОВАНИЕ
    if user['mode'] == 'image':
        if user['balance'] < PRICE_IMAGE:
            bot.send_message(message.from_user.id, "❌ Мало токенов! Для рисования нужно 30 токенов.")
            return

        bot.send_chat_action(message.chat.id, 'upload_photo')
        try:
            prompt = message.text
            # Используем сервис Pollinations.ai для бесплатного рисования
            url = f"https://image.pollinations.ai/prompt/{prompt}"
            bot.send_photo(message.chat.id, url, caption=f"🖼 {prompt}")
            update_user(message.from_user.id, balance=user['balance'] - PRICE_IMAGE)
            return
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка картинки: {e}")
            return
            
    # 2. ЧАТ (LLM с памятью)
    else:
        if user['balance'] < PRICE_TEXT:
            bot.send_message(message.from_user.id, "❌ Мало токенов! Попроси администратора пополнить твой баланс.")
            return
        process_llm_response(message, user, message.text, PRICE_TEXT)

# --- ПРОЧИЕ ХЕНДЛЕРЫ ---
@bot.message_handler(commands=['add'])
def add_tokens(message):
    if message.from_user.id != ADMIN_ID: 
        bot.reply_to(message, "Ты не мой администратор. Не наглей.")
        return
    try:
        parts = message.text.split()
        if len(parts) < 3:
            bot.reply_to(message, "Использование: /add [user_id] [amount]. Пример: /add 1234567 5000")
            return
        
        target_user_id = int(parts[1])
        amount = int(parts[2])
        
        target_user = get_user_data(target_user_id)
        new_balance = target_user['balance'] + amount
        update_user(target_user_id, balance=new_balance)
        
        bot.reply_to(message, f"✅ Пользователю {target_user_id} добавлено {amount} токенов. Новый баланс: {new_balance}")
        bot.send_message(target_user_id, f"🥳 Администратор начислил вам {amount} токенов!")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка админ-команды: {e}")

@bot.message_handler(commands=['start'])
def start_message(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🤖 Чат с AI"), types.KeyboardButton("🎨 Рисование"))
    markup.add(types.KeyboardButton("👤 Баланс"), types.KeyboardButton("⚙️ Настройки"))
    
    bot.send_message(message.chat.id, "Привет! Я Jvit, твой дерзкий, но гениальный помощник.", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text in ["🤖 Чат с AI", "🎨 Рисование", "👤 Баланс", "⚙️ Настройки"])
def menu_handler(message):
    user_id = message.from_user.id
    user = get_user_data(user_id)
    
    if message.text == "🤖 Чат с AI":
        update_user(user_id, mode='gpt')
        bot.send_message(message.chat.id, f"✅ Режим: Чат.\n🧠 Модель: {MODELS.get(user['current_model'], 'gpt-4o-mini').upper()}")
    elif message.text == "🎨 Рисование":
        update_user(user_id, mode='image')
        bot.send_message(message.chat.id, "✅ Режим: Рисование")
    elif user['mode'] == 'image': 
        update_user(user_id, mode='gpt')
    elif message.text == "👤 Баланс":
        bot.send_message(message.chat.id, f"💰 Баланс: {user['balance']} токенов.")
    elif message.text == "⚙️ Настройки":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("GPT-4o Mini", callback_data='set_model_chat'),
                   types.InlineKeyboardButton("GPT-3.5", callback_data='set_model_backup'))
        
        bot.send_message(message.chat.id, f"Выбери нейросеть:\nСейчас: {MODELS.get(user['current_model'], 'gpt-4o-mini').upper()}", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('set_model_'))
def callback_model(call):
    model_key = call.data.replace('set_model_', '')
    new_model = 'chat' if model_key == 'chat' else 'backup'
    update_user(call.from_user.id, current_model=new_model)
    
    bot.answer_callback_query(call.id, "Сохранено!")
    bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, 
                          text=f"✅ Выбрана: {MODELS.get(new_model, 'gpt-4o-mini').upper()}")

@bot.message_handler(content_types=['photo', 'video', 'document'])
@bot.message_handler(func=lambda message: True)
def handle_media(message):
    pass 

# --- КРИТИЧЕСКАЯ ЗАДЕРЖКА ДЛЯ УСТРАНЕНИЯ КОНФЛИКТА AMVERA ---
print("--- ⏳ ОЖИДАНИЕ ПЕРЕД ЗАПУСКОМ (10 СЕКУНД) ---") 
time.sleep(10) 
print("Бот (Jvit) запускается...")

bot.infinity_polling()
