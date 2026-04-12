import pandas as pd

def process_option_chain(data):
    """
    Processes the raw JSON from the NSE API into a structured format.
    Returns:
        df: Pandas DataFrame containing granular options data for all strikes.
        market_stats: Dictionary containing total OI and global market statistics.
    """
    if not data or 'records' not in data:
        return None, None
    
    timestamp = data['records']['timestamp']
    records_data = data['records']['data']
    
    spot_price = None
    if len(records_data) > 0:
        # Check if underlyingValue is at the root of a record
        if 'CE' in records_data[0] and 'underlyingValue' in records_data[0]['CE']:
            spot_price = records_data[0]['CE']['underlyingValue']
        elif 'PE' in records_data[0] and 'underlyingValue' in records_data[0]['PE']:
            spot_price = records_data[0]['PE']['underlyingValue']
            
    if spot_price is None:
        # Some structure might have underlyingValue at the root records element
        spot_price = data.get('records', {}).get('underlyingValue', 0.0)
    
    processed_records = []
    
    for item in records_data:
        strike_price = item.get('strikePrice')
        
        ce_data = item.get('CE', {})
        pe_data = item.get('PE', {})
        
        # NSE returns missing keys as if there's no CE or PE data for specific strikes.
        # Fallback to 0.
        ce_oi = ce_data.get('openInterest', 0)
        pe_oi = pe_data.get('openInterest', 0)
        
        ce_oi_change = ce_data.get('changeinOpenInterest', 0)
        pe_oi_change = pe_data.get('changeinOpenInterest', 0)
        
        ce_volume = ce_data.get('totalTradedVolume', 0)
        pe_volume = pe_data.get('totalTradedVolume', 0)
        
        processed_records.append({
            'timestamp': timestamp,
            'spot_price': spot_price,
            'strike_price': strike_price,
            'ce_oi': ce_oi,
            'pe_oi': pe_oi,
            'ce_oi_change': ce_oi_change,
            'pe_oi_change': pe_oi_change,
            'ce_volume': ce_volume,
            'pe_volume': pe_volume
        })
        
    df = pd.DataFrame(processed_records)
    
    # Calculate Market Stats
    total_ce_oi = df['ce_oi'].sum()
    total_pe_oi = df['pe_oi'].sum()
    
    pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 0
    
    # Using idxmax properly
    highest_ce_oi_strike = df.loc[df['ce_oi'].idxmax()]['strike_price'] if not df.empty and total_ce_oi > 0 else 0
    highest_pe_oi_strike = df.loc[df['pe_oi'].idxmax()]['strike_price'] if not df.empty and total_pe_oi > 0 else 0
    
    market_stats = {
        'timestamp': timestamp,
        'spot_price': spot_price,
        'total_ce_oi': total_ce_oi,
        'total_pe_oi': total_pe_oi,
        'pcr': pcr,
        'highest_ce_oi_strike': highest_ce_oi_strike,
        'highest_pe_oi_strike': highest_pe_oi_strike
    }
    
    return df, market_stats
