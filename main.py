import os
import asyncio
from datetime import date
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiomysql
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import CommandStart
from aiogram.utils.web_app import safe_parse_webapp_init_data

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "db": os.getenv("DB_NAME"),
    "autocommit": True
}

# --- НАСТРОЙКА БАЗЫ ДАННЫХ И FASTAPI ---
db_pool = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    db_pool = await aiomysql.create_pool(**DB_CONFIG)
    # Запускаем бота в фоновом режиме при старте FastAPI
    asyncio.create_task(dp.start_polling(bot))
    yield
    # Корректное закрытие ресурсов при выключении
    await dp.storage.close()
    await bot.session.close()
    db_pool.close()
    await db_pool.wait_closed()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ternomap.github.io"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SaveEventRequest(BaseModel):
    initData: str
    event_date: date
    text: str

class GetEventsRequest(BaseModel):
    initData: str

def verify_tg_user(init_data_str: str) -> int:
    try:
        validated_data = safe_parse_webapp_init_data(token=BOT_TOKEN, init_data=init_data_str)
        return validated_data.user.id
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ошибка авторизации Telegram")

# Эндпоинт для раздачи фронтенда index.html
@app.get("/")
async def serve_index():
    return FileResponse("index.html")

@app.post("/api/events/save")
async def save_event(payload: SaveEventRequest):
    user_id = verify_tg_user(payload.initData)
    
    if payload.event_date < date.today():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Нельзя ставить события в прошлое!")
        
    async with db_pool.acquire() as conn:
        async with conn.cursor() as cur:
            sql = "INSERT INTO user_events (user_id, event_date, event_text) VALUES (%s, %s, %s)"
            await cur.execute(sql, (user_id, payload.event_date, payload.text))
    return {"status": "success"}

@app.post("/api/events/get")
async def get_events(payload: GetEventsRequest):
    user_id = verify_tg_user(payload.initData)
    
    async with db_pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            sql = "SELECT event_date, event_text FROM user_events WHERE user_id = %s"
            await cur.execute(sql, (user_id,))
            rows = await cur.fetchall()
            
    formatted_events = {}
    for row in rows:
        date_str = str(row["event_date"])
        if date_str not in formatted_events:
            formatted_events[date_str] = []
        formatted_events[date_str].append(row["event_text"])
    return formatted_events


# --- НАСТРОЙКА АИОГРАМ БОТА ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    mini_app_url = os.getenv("MINI_APP_URL", "https://ternomap.github.io/tg-calendar/")
    
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Открыть Календарь", web_app=WebAppInfo(url=mini_app_url))]
    ])
    
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n\n"
        "Нажми кнопку ниже, чтобы открыть календарь событий.",
        reply_markup=markup
    )

dp.include_router(router)
