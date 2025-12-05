import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# --- 1. Configuration & Secrets ---

st.set_page_config(layout="wide", page_title="Stock Valuation Analysis", page_icon="🩵")

# Attempt to get API Key
try:
    FMP_API_KEY = st.secrets.get("FMP_API_KEY")
except FileNotFoundError:
    FMP_API_KEY = None

# Sidebar input for API key if missing (optional)
if not FMP_API_KEY:
    with st.sidebar:
        with st.expander("⚙️ API Settings"):
            st.warning("⚠️ No FMP API Key found in secrets")
            FMP_API_KEY = st.text_input("Enter FMP Key (Optional)", type="password")

# --- 2. Session State Initialization ---

if 'recent_searches' not in st.session_state:
    st.session_state.recent_searches = pd.DataFrame(
        columns=["Ticker", "Company", "Price", "Trailing PE", "PEG Ratio"]
    )

# Initialize parameter storage
if 'current_ticker' not in st.session_state:
    st.session_state.current_ticker = None

# --- 3. Core Data Functions ---

@st.cache_data(ttl=3600)
def get_stock_data(ticker, api_key=None):
    """
    Fetches all necessary data for a single stock.
    """
    yf_stock = yf.Ticker(ticker)
    
    # 1. YFinance Basic Data
    yf_info = yf_stock.info
    
    # Check if data is valid
    if not yf_info or 'symbol' not in yf_info:
        return None

    data = {
        "name": yf_info.get('longName', yf_info.get('shortName', ticker)),
        "price": yf_info.get('currentPrice', yf_info.get('regularMarketPrice', 0)),
        "beta": yf_info.get('beta', 'N/A'),
        "eps_ttm": yf_info.get('trailingEps', 0),
        "eps_fwd": yf_info.get('forwardEps', 0),
        "pe_ttm": yf_info.get('trailingPE', 0),
        "pe_fwd": yf_info.get('forwardPE', 0),
        "market_cap": yf_info.get('marketCap', 0),
        "revenue_ttm": yf_info.get('totalRevenue', 0),
        "profit_margin": yf_info.get('profitMargins', 0),
        "gross_margin": yf_info.get('grossMargins', 0),
        "operating_margin": yf_info.get('operatingMargins', 0),
        "roe": yf_info.get('returnOnEquity', 0),
        "roa": yf_info.get('returnOnAssets', 0),
        "free_cash_flow": yf_info.get('freeCashflow', 0),
        "operating_cash_flow": yf_info.get('operatingCashflow', 0),
        "debt_to_equity": yf_info.get('debtToEquity', 0),
        "current_ratio": yf_info.get('currentRatio', 0),
        "quick_ratio": yf_info.get('quickRatio', 0),
        "dividend_yield": yf_info.get('dividendYield', 0),
        "payout_ratio": yf_info.get('payoutRatio', 0),
        "price_to_book": yf_info.get('priceToBook', 0),
        "peg_ratio": yf_info.get('pegRatio', 0),
    }
    
    # Calculate P/FCF
    if data['free_cash_flow'] and data['market_cap'] and data['free_cash_flow'] > 0:
        data['p_fcf'] = data['market_cap'] / data['free_cash_flow']
    else:
        data['p_fcf'] = 0
    
    # 2. Historical Price Data (5y)
    try:
        hist_price = yf_stock.history(period="5y")
        if not hist_price.empty:
            data["hist_price"] = hist_price['Close']
        else:
            data["hist_price"] = pd.Series()
    except Exception:
        data["hist_price"] = pd.Series()
    
    # 3. Calculate Historical PE
    try:
        if not data["hist_price"].empty and data.get('pe_ttm') and data['pe_ttm'] > 0 and data['price'] > 0:
            quarterly_price = data["hist_price"].resample('Q').last()
            hist_pe = (quarterly_price / data['price']) * data['pe_ttm']
            hist_pe = hist_pe[(hist_pe > 5) & (hist_pe < 200)] # Filter outliers
            data["hist_pe"] = hist_pe
        else:
            data["hist_pe"] = pd.Series()
    except Exception:
        data["hist_pe"] = pd.Series()
    
    # 4. Analyst Growth Estimates
    growth_rate = None
    
    # Method 1: Calc from Forward/Trailing EPS
    if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
        growth_rate = ((data['eps_fwd'] - data['eps_ttm']) / data['eps_ttm']) * 100
    
    # Method 2: FMP API
    if (growth_rate is None or abs(growth_rate) > 100) and api_key:
        url_g = f"https://financialmodelingprep.com/api/v3/analyst-estimates/{ticker}?apikey={api_key}"
        try:
            g_response = requests.get(url_g, timeout=5)
            if g_response.status_code == 200:
                g_data = g_response.json()
                if isinstance(g_data, list) and len(g_data) > 0:
                    est_eps = g_data[0].get('estimatedEpsAvg', 0)
                    if est_eps and est_eps > 0 and data['eps_ttm'] > 0:
                        growth_rate = ((est_eps - data['eps_ttm']) / data['eps_ttm']) * 100
        except:
            pass
    
    # Method 3: YFinance Growth
    if growth_rate is None:
        try:
            growth_5y = yf_info.get('earningsQuarterlyGrowth', None)
            if growth_5y:
                growth_rate = growth_5y * 100
        except:
            pass
    
    if growth_rate is None:
        growth_rate = 10.0
    
    data["g_consensus"] = max(-50.0, min(growth_rate, 200.0))
    
    # 6. Analyst Targets
    try:
        analyst_info = yf_info.get('targetMeanPrice', None)
        data["analyst_target"] = {
            'mean': analyst_info if analyst_info else 0,
            'high': yf_info.get('targetHighPrice', 0),
            'low': yf_info.get('targetLowPrice', 0),
            'count': yf_info.get('numberOfAnalystOpinions', 0)
        }
        
        if api_key:
            url_rating = f"https://financialmodelingprep.com/api/v3/rating/{ticker}?apikey={api_key}"
            try:
                rating_response = requests.get(url_rating, timeout=5)
                if rating_response.status_code == 200:
                    rating_data = rating_response.json()
                    if isinstance(rating_data, list) and len(rating_data) > 0:
                        data["analyst_rating"] = {'recommendation': rating_data[0].get('rating', 'N/A')}
                    else:
                        data["analyst_rating"] = {'recommendation': 'N/A'}
            except:
                data["analyst_rating"] = {'recommendation': 'N/A'}
        else:
             data["analyst_rating"] = {'recommendation': 'N/A'}
            
    except Exception:
        data["analyst_target"] = {'mean': 0, 'high': 0, 'low': 0, 'count': 0}
        data["analyst_rating"] = {'recommendation': 'N/A'}
    
    return data

def update_recent_list(ticker, data):
    """Update recent searches"""
    new_entry = {
        "Ticker": ticker.upper(),
        "Company": data['name'][:20] + "..." if len(data['name']) > 20 else data['name'],
        "Price": f"${data['price']:.2f}",
        "Trailing PE": f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') else "N/A",
        "PEG Ratio": f"{(data['pe_fwd']/data['g_consensus']):.2f}" if data.get('pe_fwd') and data['g_consensus'] else "N/A"
    }
    
    new_df = pd.DataFrame([new_entry])
    
    if "Ticker" in st.session_state.recent_searches.columns:
        st.session_state.recent_searches = st.session_state.recent_searches[
            st.session_state.recent_searches['Ticker'] != ticker.upper()
        ]
    
    st.session_state.recent_searches = pd.concat(
        [new_df, st.session_state.recent_searches],
        ignore_index=True
    ).head(10)

# --- 4. Sidebar ---

st.sidebar.title("🩵 Equity Valuation Analysis")
st.sidebar.caption("Powered by Streamlit")

# Search Input
ticker_input = st.sidebar.text_input("Enter Ticker Symbol", key="ticker_input_sidebar").strip().upper()
search_triggered = st.sidebar.button("🔍 Search", use_container_width=True, type="primary")

# --- CRITICAL FIX: Handle Search Logic with Session State ---
if search_triggered and ticker_input:
    st.session_state.current_ticker = ticker_input

# Display Recent Searches
st.sidebar.divider()
st.sidebar.subheader("Recent Searches")
if not st.session_state.recent_searches.empty:
    st.sidebar.dataframe(st.session_state.recent_searches, width=400, hide_index=True)
else:
    st.sidebar.info("No recent searches")

# --- 5. Main Dashboard ---

# Use session state ticker instead of just the input
ticker = st.session_state.current_ticker

if ticker:
    # Fetch Data
    # Use spinner only if we are actually loading new data, though st.cache_data handles speed
    with st.spinner(f"Fetching data for {ticker}..."):
        data = get_stock_data(ticker, FMP_API_KEY)

    if not data or data['price'] == 0:
        st.error(f"❌ Unable to fetch valid data for {ticker}. Please check the symbol.")
    else:
        update_recent_list(ticker, data)

        # --- A. Core Metrics ---
        st.header(f"📈 {data['name']} ({ticker})")
        
        cols_metrics = st.columns(4)
        cols_metrics[0].metric("💰 Price", f"${data['price']:.2f}")
        cols_metrics[1].metric("📊 P/E (TTM)", f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') else "N/A")
        cols_metrics[2].metric("🔮 Forward P/E", f"{data['pe_fwd']:.2f}x" if data.get('pe_fwd') else "N/A")
        cols_metrics[3].metric("⚡ Beta", f"{data['beta']:.2f}" if isinstance(data.get('beta'), (int, float)) else "N/A")
        
        # Row 2: EPS
        cols_eps = st.columns(4)
        cols_eps[0].metric("💵 EPS (TTM)", f"${data['eps_ttm']:.2f}" if data['eps_ttm'] else "N/A")
        cols_eps[1].metric("🎯 Forward EPS", f"${data['eps_fwd']:.2f}" if data['eps_fwd'] else "N/A")
        
        eps_growth = 0
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
            eps_growth = ((data['eps_fwd'] - data['eps_ttm']) / data['eps_ttm']) * 100
            cols_eps[2].metric("📈 Implied Growth", f"{eps_growth:.1f}%", 
                              help="Growth implied by Forward vs TTM EPS")
        else:
            cols_eps[2].metric("📈 Implied Growth", "N/A")
        
        cols_eps[3].metric("🏦 Analyst Growth", f"{data['g_consensus']:.1f}%")
        
        # --- Data Quality Check ---
        st.divider()
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
            eps_ratio = data['eps_fwd'] / data['eps_ttm']
            if eps_ratio > 1.5:
                st.error(f"⚠️ **Data Warning**: Forward EPS is {eps_ratio:.1f}x higher than Trailing EPS. Trailing P/E may be misleading. Use Forward P/E.")
            elif eps_ratio > 1.2:
                st.warning(f"💡 Note: Forward EPS is notably higher than Trailing EPS. Growth expectations are high.")

        # Correction for Forward EPS if needed
        fwd_eps_display = data['eps_fwd']
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_fwd'] < data['eps_ttm'] * 0.5:
             if data['g_consensus'] and data['g_consensus'] > -30:
                fwd_eps_display = data['eps_ttm'] * (1 + data['g_consensus']/100)
                st.info(f"💡 Forward EPS Adjusted based on growth rate: ${fwd_eps_display:.2f}")

        # Row 3: Financials
        cols_value = st.columns(4)
        def fmt_mc(v):
            if v >= 1e12: return f"${v/1e12:.2f}T"
            if v >= 1e9: return f"${v/1e9:.2f}B"
            return f"${v/1e6:.2f}M"

        cols_value[0].metric("🏢 Market Cap", fmt_mc(data['market_cap']) if data['market_cap'] else "N/A")
        cols_value[1].metric("📊 Revenue", fmt_mc(data['revenue_ttm']) if data['revenue_ttm'] else "N/A")
        cols_value[2].metric("💹 Profit Margin", f"{data['profit_margin']*100:.1f}%" if data['profit_margin'] else "N/A")
        cols_value[3].metric("💸 P/FCF", f"{data['p_fcf']:.1f}x" if data['p_fcf'] else "N/A")

        # --- Detailed Data Expander ---
        with st.expander("📋 View Complete Financial Data"):
            st.markdown("### 💰 Profitability")
            p_cols = st.columns(4)
            p_cols[0].metric("ROE", f"{data['roe']*100:.1f}%" if data['roe'] else "N/A")
            p_cols[1].metric("ROA", f"{data['roa']*100:.1f}%" if data['roa'] else "N/A")
            p_cols[2].metric("Gross Margin", f"{data['gross_margin']*100:.1f}%" if data['gross_margin'] else "N/A")
            p_cols[3].metric("Op Margin", f"{data['operating_margin']*100:.1f}%" if data['operating_margin'] else "N/A")
            
            st.divider()
            st.markdown("### ⚖️ Financial Health")
            h_cols = st.columns(3)
            h_cols[0].metric("Debt/Equity", f"{data['debt_to_equity']:.2f}" if data['debt_to_equity'] else "N/A")
            h_cols[1].metric("Current Ratio", f"{data['current_ratio']:.2f}" if data['current_ratio'] else "N/A")
            
            # Health Score
            health_score = 0
            if data['debt_to_equity'] and data['debt_to_equity'] < 1.0: health_score += 1
            if data['current_ratio'] and data['current_ratio'] > 1.5: health_score += 1
            if data['free_cash_flow'] and data['free_cash_flow'] > 0: health_score += 1
            
            if health_score >= 2:
                st.success(f"✅ Health Score: {health_score}/3 (Good)")
            else:
                st.warning(f"⚠️ Health Score: {health_score}/3 (Caution)")

        st.divider()

        # --- B. Valuation Analysis ---
        st.header("💎 Valuation Analysis")
        st.markdown(f"### Current Price: **${data['price']:.2f}**")
        
        # --- B1. P/E Model ---
        st.subheader("💰 1. Forward P/E Valuation")
        
        # Calculate PE Stats
        if not data['hist_pe'].empty:
            pe_mean = data['hist_pe'].mean()
            pe_std = data['hist_pe'].std()
            pe_low_rec = max(5, pe_mean - pe_std)
            pe_mid_rec = pe_mean
            pe_high_rec = pe_mean + pe_std
        else:
            pe_mean = data['pe_fwd'] if data['pe_fwd'] else 20
            pe_std = pe_mean * 0.3
            pe_low_rec = pe_mean * 0.7
            pe_mid_rec = pe_mean
            pe_high_rec = pe_mean * 1.3

        st.info(f"**System Recommendation (Based on history):** Low: {pe_low_rec:.1f}x | Fair: {pe_mid_rec:.1f}x | High: {pe_high_rec:.1f}x")

        # Interactive Inputs (Now safe because of Session State)
        pe_c1, pe_c2, pe_c3 = st.columns(3)
        pe_low = pe_c1.number_input("🟢 Low P/E", value=float(round(pe_low_rec, 1)), step=0.5, key="pe_low")
        pe_mid = pe_c2.number_input("🟡 Fair P/E", value=float(round(pe_mid_rec, 1)), step=0.5, key="pe_mid")
        pe_high = pe_c3.number_input("🔴 High P/E", value=float(round(pe_high_rec, 1)), step=0.5, key="pe_high")

        if fwd_eps_display and fwd_eps_display > 0:
            price_low = pe_low * fwd_eps_display
            price_mid = pe_mid * fwd_eps_display
            price_high = pe_high * fwd_eps_display
            
            res_c1, res_c2, res_c3 = st.columns(3)
            res_c1.metric("🟢 Undervalued", f"${price_low:.2f}", delta=f"{(price_low/data['price'] - 1)*100:+.1f}%")
            res_c2.metric("🟡 Fair Value", f"${price_mid:.2f}", delta=f"{(price_mid/data['price'] - 1)*100:+.1f}%")
            res_c3.metric("🔴 Overvalued", f"${price_high:.2f}", delta=f"{(price_high/data['price'] - 1)*100:+.1f}%")
            
            # Gauge Chart
            fig_gauge = go.Figure()
            fig_gauge.add_trace(go.Bar(
                x=['Undervalued', 'Fair', 'Overvalued'],
                y=[price_low, price_mid, price_high],
                marker_color=['green', 'gold', 'red'],
                text=[f'${price_low:.0f}', f'${price_mid:.0f}', f'${price_high:.0f}'],
                textposition='auto'
            ))
            fig_gauge.add_hline(y=data['price'], line_dash="dash", line_color="blue", annotation_text=f"Current: ${data['price']:.2f}")
            fig_gauge.update_layout(height=300, margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig_gauge, use_container_width=True)
        else:
            st.error("Missing Forward EPS data for P/E valuation.")

        st.divider()

        # --- B2. PEG Model ---
        st.subheader("🚀 2. PEG Ratio Analysis")
        
        # Growth Rate Inputs
        g_h_default = 10.0 # Default fallback
        # Try to calc historical CAGR
        if not data['hist_price'].empty and len(data['hist_price']) > 200:
             start_p = data['hist_price'].iloc[0]
             end_p = data['hist_price'].iloc[-1]
             if start_p > 0:
                 g_h_default = ((end_p/start_p)**(1/5) - 1) * 100
        
        st.markdown("#### Growth Assumptions")
        g_col1, g_col2 = st.columns(2)
        
        with g_col1:
            st.caption("Weighted Growth Rate Calculator")
            g_hist_in = st.number_input("Historical Growth %", value=float(round(g_h_default, 1)), step=0.5, key="g_hist_in")
            weight_in = st.slider("Analyst Weight", 0.0, 1.0, 0.7, 0.1, key="w_in")
            
            g_blended = (data['g_consensus'] * weight_in) + (g_hist_in * (1 - weight_in))
            st.metric("Blended Growth Rate", f"{g_blended:.1f}%")
        
        with g_col2:
            st.caption("PEG Assessment")
            if g_blended > 0 and data['pe_fwd'] > 0:
                peg = data['pe_fwd'] / g_blended
                st.metric("Forward PEG", f"{peg:.2f}x")
                
                if peg < 0.8: st.success("✅ Undervalued (< 0.8)")
                elif peg < 1.2: st.info("🟡 Fair Value (0.8 - 1.2)")
                elif peg < 2.0: st.warning("🔴 Overvalued (1.2 - 2.0)")
                else: st.error("❌ Expensive (> 2.0)")
            else:
                st.write("Insufficient data for PEG.")

        st.divider()

        # --- B3. Analyst Targets ---
        st.subheader("🏦 3. Analyst Consensus")
        an_tgt = data.get('analyst_target', {})
        if an_tgt.get('mean', 0) > 0:
            ac1, ac2, ac3 = st.columns(3)
            ac1.metric("Low Target", f"${an_tgt['low']:.2f}")
            ac2.metric("Mean Target", f"${an_tgt['mean']:.2f}", delta=f"{(an_tgt['mean']/data['price'] - 1)*100:+.1f}%")
            ac3.metric("High Target", f"${an_tgt['high']:.2f}")
            st.caption(f"Based on {an_tgt['count']} analysts")
        else:
            st.info("No analyst target data available.")

        # --- C. History Charts ---
        st.divider()
        st.header("📊 5-Year History")
        
        if not data['hist_price'].empty:
            chart_c1, chart_c2 = st.columns(2)
            with chart_c1:
                st.subheader("Price History")
                st.line_chart(data['hist_price'], height=300)
            with chart_c2:
                if not data['hist_pe'].empty:
                    st.subheader("P/E History")
                    st.line_chart(data['hist_pe'], height=300)
        else:
            st.write("No historical data available.")

else:
    st.info("👈 Enter a ticker in the sidebar to start.")
    st.markdown("""
    ### How to use
    1. **Enter Ticker**: e.g., AAPL, NVDA in the sidebar.
    2. **Review Metrics**: Check P/E, EPS, and Data Quality warnings.
    3. **Adjust Valuation**: Use the +/- buttons in the Valuation section to adjust P/E multiples and Growth rates. **(Now working correctly!)**
    """)
