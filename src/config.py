import os
from dotenv import load_dotenv

load_dotenv()
BRAPI_TOKEN: str | None = os.getenv("BRAPI_TOKEN")