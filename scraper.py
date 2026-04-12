import requests
import time
from config import NSE_BASE_URL, NSE_OPTION_CHAIN_URL, HEADERS

def get_nse_data():
    """
    Fetches the live options chain data from NSE API.
    Handles session creation and retries.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Step 1: Hit the base URL to get cookies
            try:
                base_response = session.get(NSE_BASE_URL, timeout=10)
            except requests.exceptions.RequestException:
                pass
            
            time.sleep(2)  # Wait so NSE doesn't block for sudden requests
            
            # Step 2: Hit the option chain URL
            api_response = session.get(NSE_OPTION_CHAIN_URL, timeout=10)
            
            try:
                data = api_response.json()
                if not data:
                    print(f"[Scraper] Warning: NSE returned empty JSON. This often happens if cookies are rejected by WAF.")
                return data
            except Exception as json_err:
                raise requests.exceptions.RequestException("JSON Parse Error")
        except requests.exceptions.RequestException as e:
            print(f"[Scraper] Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)  # Wait before retrying
            else:
                print("[Scraper] Max retries reached. Could not fetch data.")
                return None
