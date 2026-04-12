import pandas as pd
from sqlalchemy import create_engine, Column, Integer, String, Float, MetaData, Table

from config import DB_PATH, DATABASE_URL

def get_engine():
    """Returns an SQLAlchemy engine connected to Postgres or fallback SQLite."""
    if DATABASE_URL:
        # Use PostgreSQL from Railway/Render
        # SQLAlchemy requires connection pooling tuning for some cloud workloads, 
        # but defaults work fine for a single worker doing sequential writes.
        engine = create_engine(DATABASE_URL)
    else:
        # Fallback to local SQLite DB
        engine = create_engine(f"sqlite:///{DB_PATH}")
    return engine

def init_db():
    """Initializes the database schema using SQLAlchemy."""
    engine = get_engine()
    metadata = MetaData()
    
    # Table for granular options data for each strike
    options_table = Table('options_data', metadata,
        Column('id', Integer, primary_key=True, autoincrement=True),
        Column('timestamp', String),
        Column('spot_price', Float),
        Column('strike_price', Float),
        Column('ce_oi', Float),
        Column('pe_oi', Float),
        Column('ce_oi_change', Float),
        Column('pe_oi_change', Float),
        Column('ce_volume', Integer),
        Column('pe_volume', Integer)
    )
    
    # Table for aggregated market summary and signals
    summary_table = Table('market_summary', metadata,
        Column('id', Integer, primary_key=True, autoincrement=True),
        Column('timestamp', String),
        Column('spot_price', Float),
        Column('total_ce_oi', Float),
        Column('total_pe_oi', Float),
        Column('pcr', Float),
        Column('highest_ce_oi_strike', Float),
        Column('highest_pe_oi_strike', Float),
        Column('signal', String)
    )
    
    # Create tables if they do not exist
    metadata.create_all(engine)

def save_options_data(df):
    """Saves granular options data (pandas DataFrame) to the database."""
    if df is None or df.empty:
        return
    
    engine = get_engine()
    # Pandas makes it extremely easy to push to SQLAlchemy dynamically
    df.to_sql("options_data", engine, if_exists="append", index=False)

def save_market_summary(summary_dict):
    """Saves market summary and signals to the database."""
    if not summary_dict:
        return
        
    engine = get_engine()
    # Convert dict to one-row DataFrame to utilize SQLAlchemy push easily
    df = pd.DataFrame([summary_dict])
    df.to_sql("market_summary", engine, if_exists="append", index=False)
