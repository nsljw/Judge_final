import logging
import os
from dotenv import load_dotenv

load_dotenv()

#bot settings
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

#Database settings
DATABASE = os.getcwd("DATABASE_NAME")
USER = os.getenv("DATABASE_USER")
PASSWORD = os.getenv("PASSWORD")

#logging settings
logging.basicConfig(level=logging.INFO)

#database
rooms = {}