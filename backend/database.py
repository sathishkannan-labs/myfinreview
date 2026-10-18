from sqlmodel import create_engine, Session, SQLModel
from sqlalchemy.engine import make_url
import os
from dotenv import load_dotenv
from config.settings import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

load_dotenv()

# SQLAlchemy 2.1 defaults bare PostgreSQL URLs to psycopg 3. This
# application installs psycopg2-binary, so keep driver selection explicit.
# URL objects preserve passwords and query parameters without string rewriting.
engine_url = make_url(DATABASE_URL)
if engine_url.drivername in {"postgres", "postgresql"}:
    engine_url = engine_url.set(drivername="postgresql+psycopg2")
is_sqlite = engine_url.get_backend_name() == "sqlite"

if is_sqlite:
    # SQLite-specific configuration
    engine = create_engine(
        engine_url,
        echo=False,
        connect_args={"check_same_thread": False}
    )
else:
    # Advanced pooling and keepalives for PostgreSQL (Aiven/Supabase)
    engine = create_engine(
        engine_url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=60,
        pool_size=15,
        max_overflow=5,
        connect_args={
            "connect_timeout": 30,
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5
        }
    )

def get_session():
    with Session(engine) as session:
        yield session

def init_db():
    from sqlalchemy import inspect
    # Import all models to ensure metadata is populated
    from models.portable_models import User, Holding, Transaction, Insight, NewsArticle, AlertRule, AlertEvent, AnalyticsSummary, PriceCache, NewsHash

    inspector = inspect(engine)
    if not inspector.has_table("user"):
        logger.info("Tables not found. Creating database schema.")
        SQLModel.metadata.create_all(engine)
    else:
        logger.info("Database already initialized. Skipping create_all.")
