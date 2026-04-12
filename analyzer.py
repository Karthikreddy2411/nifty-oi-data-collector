def generate_signal(market_stats):
    """
    Generates a rule-based trading signal.
    
    Rules:
    - SELL PUT: Put OI is strong (PCR > 1). This is a bullish signal.
    - SELL CALL: Call OI is strong (PCR < 1). This is a bearish signal.
    """
    signal = "NEUTRAL"
    
    if market_stats is None:
        return signal

    pcr = market_stats.get('pcr', 0)
    
    if pcr > 1.05: # Slight threshold to avoid noise
        signal = "SELL PUT"
    elif pcr < 0.95:
        signal = "SELL CALL"
        
    return signal
