import sqlite3
import pandas as pd
from config import DB_PATH

def get_connection():
    """Returns a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    # Enable robust multithreading and performance
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    """Initializes the database schema."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Table for granular options data for each strike
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS options_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            spot_price REAL,
            strike_price REAL,
            ce_oi REAL,
            pe_oi REAL,
            ce_oi_change REAL,
            pe_oi_change REAL,
            ce_volume INTEGER,
            pe_volume INTEGER
        )
    """)
    
    # Table for aggregated market summary and signals
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            spot_price REAL,
            total_ce_oi REAL,
            total_pe_oi REAL,
            pcr REAL,
            highest_ce_oi_strike REAL,
            highest_pe_oi_strike REAL,
            signal TEXT
        )
    """)
    
    conn.commit()
    conn.close()

def save_options_data(df):
    """Saves granular options data (pandas DataFrame) to the database."""
    if df is None or df.empty:
        return
    
    conn = get_connection()
    df.to_sql("options_data", conn, if_exists="append", index=False)
    conn.close()

def save_market_summary(summary_dict):
    """Saves market summary and signals to the database."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO market_summary (
            timestamp, spot_price, total_ce_oi, total_pe_oi, pcr, 
            highest_ce_oi_strike, highest_pe_oi_strike, signal
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        summary_dict.get("timestamp"),
        summary_dict.get("spot_price"),
        summary_dict.get("total_ce_oi"),
        summary_dict.get("total_pe_oi"),
        summary_dict.get("pcr"),
        summary_dict.get("highest_ce_oi_strike"),
        summary_dict.get("highest_pe_oi_strike"),
        summary_dict.get("signal")
    ))
    
    conn.commit()
    conn.close()
