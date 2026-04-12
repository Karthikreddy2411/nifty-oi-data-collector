import time
import schedule
from datetime import datetime
import pytz

from config import MARKET_START_HOUR, MARKET_START_MINUTE, MARKET_END_HOUR, MARKET_END_MINUTE
from db import init_db, save_options_data, save_market_summary
from scraper import get_nse_data
from processor import process_option_chain
from analyzer import generate_signal

def is_market_open():
    """Checks if the current time in IST is within active market hours."""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.now(ist)
    
    # Check if it's a weekday (Monday=0, Sunday=6)
    if now.weekday() > 4:
        return False
        
    start_time = now.replace(hour=MARKET_START_HOUR, minute=MARKET_START_MINUTE, second=0, microsecond=0)
    end_time = now.replace(hour=MARKET_END_HOUR, minute=MARKET_END_MINUTE, second=0, microsecond=0)
    
    return start_time <= now <= end_time

def job():
    """The main pipeline job."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting data collection pipeline...")
    
    if not is_market_open():
        print("Market is currently closed. Skipping execution.")
        return
        
    print("Fetching data from NSE...")
    raw_data = get_nse_data()
    
    if raw_data:
        print("Processing data...")
        df, market_stats = process_option_chain(raw_data)
        
        if df is not None and not df.empty:
            print("Generating signal...")
            signal = generate_signal(market_stats)
            market_stats['signal'] = signal
            
            print(f"Current Signal: {signal} (PCR: {market_stats.get('pcr', 0):.2f})")
            
            print("Saving to database...")
            save_options_data(df)
            save_market_summary(market_stats)
            print("Run complete!")
        else:
            print("Failed to process data into DataFrame.")
    else:
        print("Failed to fetch data.")

def main():
    print("Initializing Database...")
    init_db()
    
    print("Starting NIFTY Options Data Collector")
    print("Testing immediate run (ignoring market hours for first run)...")
    
    job_test()

    print("Scheduling job to run every 5 minutes...")
    schedule.every(5).minutes.do(job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

def job_test():
    """A variation of job() that ignores the market_open check for initial testing."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting preliminary test data collection...")
        
    raw_data = get_nse_data()
    
    if raw_data:
        df, market_stats = process_option_chain(raw_data)
        
        if df is not None and not df.empty:
            signal = generate_signal(market_stats)
            market_stats['signal'] = signal
            
            print(f"Test Run Signal: {signal} (PCR: {market_stats.get('pcr', 0):.2f})")
            
            save_options_data(df)
            save_market_summary(market_stats)
            print("Test run complete, DB populated.")
        else:
            print("Failed to process data.")
    else:
        print("Failed to fetch data.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopping Data Collector...")
