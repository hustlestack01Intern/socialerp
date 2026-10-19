from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

url = os.environ.get("DATABASE_URL", "postgresql+psycopg2://socialerp:socialerp@localhost:5432/socialerp")
engine = create_engine(url)
Session = sessionmaker(bind=engine)

with Session() as s:
    s.execute(text("SELECT 1"))
    s.commit()
