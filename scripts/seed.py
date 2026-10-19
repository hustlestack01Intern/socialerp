from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import os

url = os.environ.get("DATABASE_URL", "postgresql+psycopg2://socialerp:socialerp@localhost:5432/socialerp")
engine = create_engine(url)
Session = sessionmaker(bind=engine)

with Session() as s:
    s.execute(text("SELECT 1"))
    s.commit()
