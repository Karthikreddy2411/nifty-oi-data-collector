import json
import time
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from config import NSE_OPTION_CHAIN_URL

# Strategy: Use undetected Chrome to load the option chain page,
# then use JavaScript fetch() from within the page context to call
# the API. This preserves cookies and session state.

NSE_OPTION_CHAIN_PAGE = "https://www.nseindia.com/option-chain"


def get_nse_data():
    """
    Fetches the live options chain data from NSE.
    
    Uses undetected-chromedriver to:
    1. Load the option chain page (establishes Akamai session)
    2. Use in-page JavaScript fetch() to call the API
       (keeps same origin, same cookies, same session)
    """
    max_retries = 3
    
    for attempt in range(max_retries):
        print(f"[Scraper] Attempt {attempt + 1}/{max_retries} — launching Chrome...")
        
        driver = None
        try:
            options = uc.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            
            driver = uc.Chrome(options=options, version_main=146)
            
            # Step 1: Load the option chain page to establish full session
            print("[Scraper] Loading NSE option chain page...")
            driver.get(NSE_OPTION_CHAIN_PAGE)
            time.sleep(10)  # Wait for Akamai challenge to resolve
            
            # Verify the page loaded (check title)
            title = driver.title
            print(f"[Scraper] Page title: {title}")
            
            # Step 2: Use JavaScript fetch() from within the page
            # This maintains the same origin, cookies, and session
            print("[Scraper] Fetching API data via in-page JavaScript...")
            
            js_code = """
            return await (async () => {
                try {
                    const response = await fetch('/api/option-chain-indices?symbol=NIFTY', {
                        credentials: 'include',
                        headers: {
                            'Accept': 'application/json',
                            'X-Requested-With': 'XMLHttpRequest',
                            'Sec-Fetch-Site': 'same-origin',
                            'Sec-Fetch-Mode': 'cors',
                            'Sec-Fetch-Dest': 'empty'
                        }
                    });
                    const text = await response.text();
                    return {status: response.status, body: text};
                } catch(e) {
                    return {error: e.message};
                }
            })();
            """
            
            result = driver.execute_script(js_code)
            
            if not result:
                print("[Scraper] JS execute returned nothing")
                driver.quit()
                if attempt < max_retries - 1:
                    time.sleep(5)
                continue
            
            if 'error' in result:
                print(f"[Scraper] JS fetch error: {result['error']}")
                driver.quit()
                if attempt < max_retries - 1:
                    time.sleep(5)
                continue
            
            status = result.get('status')
            body = result.get('body', '')
            print(f"[Scraper] API response status: {status}, body length: {len(body)}")
            
            if not body or body.strip() in ('', '{}'):
                print(f"[Scraper] Empty API response")
                
                # Debug: print cookies
                cookies = driver.get_cookies()
                cookie_names = [c['name'] for c in cookies]
                print(f"[Scraper] Cookies: {cookie_names}")
                
                driver.quit()
                if attempt < max_retries - 1:
                    time.sleep(5)
                continue
            
            data = json.loads(body)
            
            if not data or 'records' not in data:
                keys = list(data.keys()) if data else []
                print(f"[Scraper] Unexpected response. Keys: {keys}")
                print(f"[Scraper] Body preview: {body[:300]}")
                driver.quit()
                if attempt < max_retries - 1:
                    time.sleep(5)
                continue
            
            record_count = len(data['records'].get('data', []))
            spot = 'N/A'
            if record_count > 0:
                first = data['records']['data'][0]
                spot = first.get('CE', first.get('PE', {})).get('underlyingValue', 'N/A')
            
            print(f"[Scraper] ✅ Got {record_count} records. Spot: {spot}")
            driver.quit()
            return data
            
        except json.JSONDecodeError as e:
            print(f"[Scraper] JSON parse error: {e}")
            if driver:
                try: driver.quit()
                except: pass
            if attempt < max_retries - 1:
                time.sleep(5)
        except Exception as e:
            print(f"[Scraper] Error: {type(e).__name__}: {e}")
            if driver:
                try: driver.quit()
                except: pass
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                print("[Scraper] Max retries reached.")
                return None
    
    return None
