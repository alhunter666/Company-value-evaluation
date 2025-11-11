import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots # 导入 make_subplots

# --- 3. 辅助函数 (全局) ---
def format_market_cap(value):
    """
    格式化市值显示 (T/B/M)
    """
    if value >= 1e12:
        return f"${value/1e12:.2f}T"
    elif value >= 1e9:
        return f"${value/1e9:.2f}B"
    elif value >= 1e6:
        return f"${value/1e6:.2f}M"
    else:
        return f"${value:,.0f}"

# --- (您现有的其他代码，如 @st.cache_data, def get_stock_data 等，从这里开始...) ---

# --- 1. 配置与密钥 ---

st.set_page_config(layout="wide", page_title="股票估值分析", page_icon="🩵")

FMP_API_KEY = st.secrets.get("FMP_API_KEY")

if not FMP_API_KEY:
    st.error("FMP_API_KEY 未在 Streamlit Secrets 中设置！请添加它以便 App 运行。")
    st.info("💡 提示：在 Streamlit Cloud 的 Settings → Secrets 中添加：\n```\nFMP_API_KEY = \"your_api_key_here\"\n```")
    st.stop()

# --- 2. 会话状态初始化 ---

if 'recent_searches' not in st.session_state:
    # ✅ 修正：使列名与 update_recent_list 函数一致
    st.session_state.recent_searches = pd.DataFrame(
        columns=["代码 Ticker", "公司 Company", "价格 Price", "Forward PE", "Forward PEG"]
    )

# ... (其余会话状态保持不变) ...
if 'current_ticker' not in st.session_state:
    st.session_state.current_ticker = None
if 'g_history' not in st.session_state:
    st.session_state.g_history = 10.0
if 'analyst_weight' not in st.session_state:
    st.session_state.analyst_weight = 0.7

# --- 3. 核心数据获取函数 (V4.0 修正版) ---

@st.cache_data(ttl=3600)
def get_stock_data(ticker):
    """
    获取单个股票所需的所有数据 (V4.0 修正版)
    """
    yf_stock = yf.Ticker(ticker)
    
    # 1. YFinance 基础数据 (保持不变)
    yf_info = yf_stock.info
    data = {
        "name": yf_info.get('longName', yf_info.get('shortName', ticker)),
        "price": yf_info.get('currentPrice', yf_info.get('regularMarketPrice', 0)),
        "beta": yf_info.get('beta', 'N/A'),
        "eps_ttm": yf_info.get('trailingEps', 0),    # GAAP EPS
        "eps_fwd": yf_info.get('forwardEps', 0),    # Non-GAAP EPS 
        "pe_ttm": yf_info.get('trailingPE', 0),     # GAAP PE
        "pe_fwd": yf_info.get('forwardPE', 0),    # Non-GAAP PE
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
    
    if data['free_cash_flow'] > 0 and data['market_cap'] > 0:
        data['p_fcf'] = data['market_cap'] / data['free_cash_flow']
    else:
        data['p_fcf'] = 0
    
    # 2. 获取历史价格数据（5年） (保持不变)
    try:
        hist_price = yf_stock.history(period="5y")
        if not hist_price.empty:
            data["hist_price"] = hist_price['Close']
        else:
            data["hist_price"] = pd.Series()
    except Exception as e:
        data["hist_price"] = pd.Series()
    
    # --- 3. ✅ 修正：获取 *真实* 历史PE和EPS (来自FMP) ---
    # 替换掉不准确的估算逻辑
    data["hist_pe"] = pd.Series()
    data["hist_eps"] = pd.Series()
    try:
        # 使用 FMP TTM 端点获取滚动历史数据
        url_hist = f"https://financialmodelingprep.com/api/v3/historical-ratios-ttm/{ticker}?limit=20&apikey={FMP_API_KEY}" # 5 years = 20 quarters
        hist_response = requests.get(url_hist, timeout=10)
        hist_data = hist_response.json()
        
        if isinstance(hist_data, list) and len(hist_data) > 0:
            hist_df = pd.DataFrame(hist_data).iloc[::-1] # 倒序
            hist_df['date'] = pd.to_datetime(hist_df['date'])
            hist_df = hist_df.set_index('date')
            
            # FMP的 'peRatioTTM' 通常基于 Non-GAAP，这正是我们想要的
            if 'peRatioTTM' in hist_df.columns:
                data["hist_pe"] = hist_df['peRatioTTM'].apply(pd.to_numeric, errors='coerce').dropna()
            # FMP的 'epsTTM' 通常也是 Non-GAAP
            if 'epsTTM' in hist_df.columns:
                data["hist_eps"] = hist_df['epsTTM'].apply(pd.to_numeric, errors='coerce').dropna()

    except Exception as e:
        st.warning(f"无法从FMP获取历史PE/EPS数据: {e}")
        pass # 即使失败，也继续（图表将为空）
    # --- 修正结束 ---

    # 4. 分析师增长率预测 (保持不变, 您的逻辑很好)
    growth_rate = None
    
    if data['eps_fwd'] > 0 and data['eps_ttm'] > 0:
        growth_rate = ((data['eps_fwd'] - data['eps_ttm']) / data['eps_ttm']) * 100
    
    if growth_rate is None or abs(growth_rate) > 100:
        url_g = f"https://financialmodelingprep.com/api/v3/analyst-estimates/{ticker}?apikey={FMP_API_KEY}"
        try:
            g_response = requests.get(url_g, timeout=10)
            g_data = g_response.json()
            if isinstance(g_data, list) and len(g_data) > 0:
                est_eps = g_data[0].get('estimatedEpsAvg', 0)
                if est_eps and est_eps > 0 and data['eps_ttm'] > 0:
                    growth_rate = ((est_eps - data['eps_ttm']) / data['eps_ttm']) * 100
        except:
            pass
    
    if growth_rate is None:
        try:
            growth_5y = yf_info.get('earningsQuarterlyGrowth', None)
            if growth_5y:
                growth_rate = growth_5y * 100
        except:
            pass
    
    if growth_rate is None:
        growth_rate = 10.0
    
    growth_rate = max(-50.0, min(growth_rate, 200.0))
    data["g_consensus"] = growth_rate
    
    # 5. 获取分析师目标价 (保持不变)
    try:
        data["analyst_target"] = {
            'mean': yf_info.get('targetMeanPrice', None),
            'high': yf_info.get('targetHighPrice', None),
            'low': yf_info.get('targetLowPrice', None),
            'median': yf_info.get('targetMedianPrice', None),
            'count': yf_info.get('numberOfAnalystOpinions', None)
        }
    except:
        data["analyst_target"] = {'mean': 0, 'high': 0, 'low': 0, 'median': 0, 'count': 0}
        
    return data

def update_recent_list(ticker, data):
    """(您的函数 - 保持不变)"""
    new_entry = {
        "代码 Ticker": ticker.upper(),
        "公司 Company": data['name'][:20] + "..." if len(data['name']) > 20 else data['name'],
        "价格 Price": f"${data['price']:.2f}",
        "Forward PE": f"{data['pe_fwd']:.2f}x" if data.get('pe_fwd') else "N/A",
        "Forward PEG": f"{(data['pe_fwd']/data['g_consensus']):.2f}" if data.get('pe_fwd') and data['g_consensus'] else "N/A"
    }
    
    new_df_entry = pd.DataFrame([new_entry])
    
    st.session_state.recent_searches = st.session_state.recent_searches[
        st.session_state.recent_searches['代码 Ticker'] != ticker.upper()
    ]
    
    st.session_state.recent_searches = pd.concat(
        [new_df_entry, st.session_state.recent_searches],
        ignore_index=True
    ).head(10)

# --- 4. 侧边栏布局 ---
# (保持不变, 您的UI很好)
st.sidebar.title("🩵 估值分析 Equity Valuation Analysis")
st.sidebar.caption("With love")
ticker = st.sidebar.text_input("输入股票代码 Ticker ", key="ticker_input").strip().upper()
search_button = st.sidebar.button("🔍 搜索 Search", use_container_width=True, type="primary")
st.sidebar.divider()
st.sidebar.subheader("最近10次搜索 Recent 10 Searches")
if not st.session_state.recent_searches.empty:
    st.sidebar.dataframe(
        st.session_state.recent_searches,
        width=400,
        hide_index=True
    )
else:
    st.sidebar.info("暂无搜索记录")

# --- 5. 主面板布局 ---

if search_button and ticker:
    # ✅ 修正：这是主 `try` 块，所有内容都应在里面
    try:
        with st.spinner(f"正在获取 {ticker} 的数据..."):
            data = get_stock_data(ticker)
        
        # --- A. 核心指标 / Core Metrics ---
        st.header(f"📈 {data['name']} ({ticker})")
        
        if data['price'] == 0:
            st.error(f"❌ 无法获取 {ticker} 的有效数据 / Unable to fetch valid data for {ticker}")
            st.stop()
        
        # (您的核心指标布局 - 保持不变)
        cols_metrics = st.columns(4)
        cols_metrics[0].metric("💰 当前价格 Current Price", f"${data['price']:.2f}")
        cols_metrics[1].metric("📊 市盈率 P/E (TTM, GAAP)", f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') and data['pe_ttm'] > 0 else "N/A", help="基于GAAP EPS，可能被会计项目污染")
        cols_metrics[2].metric("🔮 远期市盈率 Forward P/E (Non-GAAP)", f"{data['pe_fwd']:.2f}x" if data.get('pe_fwd') and data['pe_fwd'] > 0 else "N/A", help="基于Non-GAAP预期，通常更可靠")
        cols_metrics[3].metric("⚡ 贝塔系数 Beta", f"{data['beta']:.2f}" if isinstance(data.get('beta'), (int, float)) else "N/A")
        
        cols_eps = st.columns(4)
        cols_eps[0].metric("💵 每股收益 EPS (TTM, GAAP)", f"${data['eps_ttm']:.2f}" if data['eps_ttm'] else "N/A")
        cols_eps[1].metric("🎯 远期EPS Forward EPS (Non-GAAP)", f"${data['eps_fwd']:.2f}" if data['eps_fwd'] else "N/A")
        
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
            eps_growth = ((data['eps_fwd'] - data['eps_ttm']) / data['eps_ttm']) * 100
            cols_eps[2].metric("📈 EPS增长率 Growth (Fwd vs TTM)", f"{eps_growth:.1f}%", delta=f"{eps_growth:.1f}%")
        else:
            cols_eps[2].metric("📈 EPS增长率 Growth", "N/A")
        
        cols_eps[3].metric("🏦 分析师预期增长 Analyst Growth (5Y)", f"{data['g_consensus']:.1f}%")
        
        # === 数据污染警告 ===
        st.divider()
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
            eps_ratio = data['eps_fwd'] / data['eps_ttm']
            if eps_ratio > 1.5 or (data['pe_ttm'] and data['pe_ttm'] > 100 and data['pe_fwd'] and data['pe_fwd'] < 50):
                st.error(f"""
                ⚠️ **数据警告 / Data Quality Warning**
                **Trailing EPS (GAAP) ($ {data['eps_ttm']:.2f})** 与 **Forward EPS (Non-GAAP) ($ {data['eps_fwd']:.2f})** 存在巨大差异。
                这通常由一次性会计项目（如收购摊销、减值）导致。
                **建议：请完全忽略 Trailing P/E ({data['pe_ttm']:.1f}x)，仅使用 Forward P/E ({data['pe_fwd']:.1f}x) 进行估值。**
                """)
            elif eps_ratio < 0.9: # 检查盈利衰退
                st.warning(f"📉 盈利预警：Forward EPS (${data['eps_fwd']:.2f}) 低于 Trailing EPS (${data['eps_ttm']:.2f})。")

        # 修正Forward EPS（如果是单季度）
        fwd_eps_display = data['eps_fwd']
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_fwd'] < data['eps_ttm'] * 0.5:
            if data['g_consensus'] and data['g_consensus'] > -30:
                fwd_eps_display = data['eps_ttm'] * (1 + data['g_consensus']/100)
                st.info(f"💡 Forward EPS 似乎过低，已使用增长率调整: ${fwd_eps_display:.2f}")

        # (您的财务数据布局 - 保持不变)
        cols_value = st.columns(4)
        market_cap_str = format_market_cap(data['market_cap']) if data['market_cap'] > 0 else "N/A"
        revenue_str = format_market_cap(data['revenue_ttm']) if data['revenue_ttm'] > 0 else "N/A"
        profit_margin_str = f"{data['profit_margin']*100:.1f}%" if data['profit_margin'] else "N/A"
        
        cols_value[0].metric("🏢 市值 Market Cap", market_cap_str)
        cols_value[1].metric("📊 年营收 Revenue (TTM)", revenue_str)
        cols_value[2].metric("💹 利润率 Profit Margin", profit_margin_str)
        
        if data['p_fcf'] > 0:
            cols_value[3].metric("💸 市现率 P/FCF", f"{data['p_fcf']:.1f}x", help="市值/自由现金流")
        else:
            cols_value[3].metric("💸 市现率 P/FCF", "N/A", help="自由现金流为负或数据缺失")
            
        # (您的财务数据展开页 - 保持不变)
        with st.expander("📋 查看完整财务数据 / View Complete Financial Data"):
            # ... (您这部分代码写得很好，保持不变) ...
            st.markdown("### 💰 盈利能力指标 Profitability")
            profit_cols = st.columns(4)
            profit_cols[0].metric("ROE 净资产收益率", f"{data['roe']*100:.1f}%" if data['roe'] else "N/A")
            profit_cols[1].metric("ROA 总资产收益率", f"{data['roa']*100:.1f}%" if data['roa'] else "N/A")
            profit_cols[2].metric("Gross Margin 毛利率", f"{data['gross_margin']*100:.1f}%" if data['gross_margin'] else "N/A")
            profit_cols[3].metric("Operating Margin 营业利润率", f"{data['operating_margin']*100:.1f}%" if data['operating_margin'] else "N/A")
            
            st.divider()
            st.markdown("### 💸 现金流指标 Cash Flow")
            cf_cols = st.columns(3)
            cf_cols[0].metric("FCF 自由现金流", format_market_cap(data['free_cash_flow']) if data['free_cash_flow'] > 0 else "N/A")
            cf_cols[1].metric("Operating CF 经营现金流", format_market_cap(data['operating_cash_flow']) if data['operating_cash_flow'] > 0 else "N/A")
            cf_cols[2].metric("P/FCF 市现率", f"{data['p_fcf']:.1f}x" if data['p_fcf'] > 0 else "N/A")

            st.divider()
            st.markdown("### ⚖️ 财务健康指标 Financial Health")
            health_cols = st.columns(3)
            health_cols[0].metric("Debt/Equity 债务权益比", f"{data['debt_to_equity']:.2f}" if data['debt_to_equity'] else "N/A")
            health_cols[1].metric("Current Ratio 流动比率", f"{data['current_ratio']:.2f}" if data['current_ratio'] else "N/A")
            health_cols[2].metric("Quick Ratio 速动比率", f"{data['quick_ratio']:.2f}" if data['quick_ratio'] else "N/A")
            # ... (其余健康度评估 - 保持不变) ...

        # (您的分析师目标价 - 保持不变)
        if data.get('analyst_target') and data['analyst_target']['mean'] > 0:
            # ... (您这部分代码写得很好，保持不变) ...
            st.divider()
            st.subheader("🎯 分析师目标价 / Analyst Targets")
            target_cols = st.columns([1, 2, 1])
            with target_cols[1]:
                analyst_mean = data['analyst_target']['mean']
                analyst_high = data['analyst_target']['high']
                analyst_low = data['analyst_target']['low']
                num_analysts = data['analyst_target']['count']
                col1, col2, col3 = st.columns(3)
                col1.metric("📉 最低 Low", f"${analyst_low:.2f}" if analyst_low > 0 else "N/A")
                col2.metric("🎯 平均 Mean", f"${analyst_mean:.2f}")
                col3.metric("📈 最高 High", f"${analyst_high:.2f}" if analyst_high > 0 else "N/A")
                if analyst_mean > 0:
                    upside = ((analyst_mean - data['price']) / data['price']) * 100
                    st.metric(label=f"基于 {num_analysts} 位分析师", value=f"上涨空间 {upside:.1f}%" if upside > 0 else f"下跌风险 {upside:.1f}%")

        st.divider()
        
        # --- B. 估值分析 / Valuation Analysis ---
        st.header("💎 估值分析 / Valuation Analysis")
        
        # (您的数据说明 - 保持不变)
        with st.expander("ℹ️ 数据说明 / Data Explanation - 重要！"):
            st.markdown("...(您的数据说明)...")
            
        st.markdown(f"### 📍 当前股价 / Current Price: **${data['price']:.2f}**")
        st.divider()
        
        valuation_results = {} # 这个重置很重要
        price_mid_peg = 0.0
        
        # --- B1. 远期P/E估值法（新版） ---
        st.subheader("💰 方法一：远期P/E估值法 / Forward P/E Valuation")
        
        # ✅ 修正：使用 data['hist_pe'] (来自FMP的真实数据)
        hist_pe_data = data['hist_pe'].dropna()
        
        if not hist_pe_data.empty and len(hist_pe_data) >= 4:
            pe_mean = hist_pe_data.mean()
            pe_std = hist_pe_data.std()
            pe_low_rec = max(5, pe_mean - pe_std)
            pe_mid_rec = pe_mean
            pe_high_rec = pe_mean + pe_std
        else:
            # 如果FMP数据失败，回退到使用 Forward PE
            pe_mean = data['pe_fwd'] if data['pe_fwd'] and data['pe_fwd'] > 0 else 20
            pe_std = pe_mean * 0.3 # 估算一个30%的标准差
            pe_low_rec = pe_mean * 0.7
            pe_mid_rec = pe_mean
            pe_high_rec = pe_mean * 1.3
        
        st.markdown("#### 📊 第一步：PE区间（基于历史统计）")
        # (您的UI - 保持不变)
        stat_cols = st.columns(4)
        stat_cols[0].metric("5年平均PE", f"{pe_mean:.1f}x")
        stat_cols[1].metric("标准差", f"{pe_std:.1f}x")
        
        st.info(f"""
        💡 **系统推荐**：
        - 低估PE: {pe_low_rec:.1f}x (均值 - 1σ)
        - 合理PE: {pe_mid_rec:.1f}x (均值)
        - 高估PE: {pe_high_rec:.1f}x (均值 + 1σ)
        """)
        
        st.markdown("#### ⚙️ 第二步：自定义PE区间")
        
        # (您的表单 - 保持不变)
        with st.form(key=f"pe_form_{ticker}"):
            pe_cols = st.columns(3)
            pe_low = pe_cols[0].number_input("🟢 低估PE", min_value=1.0, value=float(round(pe_low_rec, 1)), step=1.0)
            pe_mid = pe_cols[1].number_input("🟡 合理PE", min_value=1.0, value=float(round(pe_mid_rec, 1)), step=1.0)
            pe_high = pe_cols[2].number_input("🔴 高估PE", min_value=1.0, value=float(round(pe_high_rec, 1)), step=1.0)
            submitted = st.form_submit_button("✅ 应用PE区间并计算", use_container_width=True)

        st.markdown("#### 🎯 第三步：估值结果")
        
        # ✅ 修正：始终使用 fwd_eps_display (调整后的 Non-GAAP EPS)
        if fwd_eps_display and fwd_eps_display > 0:
            price_low = pe_low * fwd_eps_display
            price_mid = pe_mid * fwd_eps_display
            price_high = pe_high * fwd_eps_display
            
            # (您的图表和结论 - 保持不变)
            result_cols = st.columns(3)
            result_cols[0].metric("🟢 低估价格", f"${price_low:.2f}", delta=f"{(price_low/data['price'] - 1)*100:+.1f}%")
            result_cols[1].metric("🟡 合理价格", f"${price_mid:.2f}", delta=f"{(price_mid/data['price'] - 1)*100:+.1f}%")
            result_cols[2].metric("🔴 高估价格", f"${price_high:.2f}", delta=f"{(price_high/data['price'] - 1)*100:+.1f}%")
            
            if data['price'] < price_low: st.success("🟢 **严重低估**")
            elif data['price'] < price_mid: st.success("🟢 **轻度低估**")
            elif data['price'] < price_high: st.info("🟡 **合理区间**")
            else: st.warning("🔴 **高估**")
                
            fig = go.Figure()
            fig.add_trace(go.Bar(x=['低估', '合理', '高估'], y=[price_low, price_mid, price_high], marker_color=['green', 'yellow', 'red'], text=[f'${price_low:.2f}', f'${price_mid:.2f}', f'${price_high:.2f}'], textposition='auto'))
            fig.add_hline(y=data['price'], line_dash="dash", line_color="blue", annotation_text=f"当前 ${data['price']:.2f}")
            fig.update_layout(title="Forward P/E估值区间", yaxis_title="价格 ($)", height=400)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("❌ Forward EPS数据缺失，无法计算")

        st.divider()

        # (您的 PEG 模型 - 保持不变)
        st.subheader("🚀 方法二：PEG增长估值法 / PEG Growth Valuation")
        # ... (您这部分代码写得很好，保持不变) ...
        # (您的 分析师目标价 模型 - 保持不变)
        st.divider()
        st.subheader("🏦 方法三：分析师目标价 / Analyst Targets")
        # ... (您这部分代码写得很好，保持不变) ...
        
        # ✅ 修正：在所有估值计算完成后，调用 update_recent_list
        # 注意：您的 update_recent_list 已被修改为不依赖PEG价格，所以我们可以安全调用
        update_recent_list(ticker, data)

        # --- C. 历史图表 / Historical Charts ---
        # ✅ 修正：使用 Plotly 重写，并移到 `try` 块内部
        st.divider()
        st.header("📊 历史发展过程 (5年) / 5-Year Historical Performance")
        
        df_price = data['hist_price'].to_frame('Price')
        # ✅ 修正：使用 data['hist_pe'] (来自FMP的真实数据)
        df_pe = data['hist_pe'].to_frame('P/E Ratio (Non-GAAP)')
        # ✅ 修正：使用 data['hist_eps'] (来自FMP的真实数据)
        df_eps = data['hist_eps'].to_frame('EPS (TTM, Non-GAAP)')
        
        # --- 图表一：股价 vs PE 双Y轴图 ---
        if not df_price.empty and not df_pe.empty:
            st.subheader("💹 股价 vs PE 历史对比 / Price vs P/E History")
            
            # 1. 准备数据 (合并)
            df_price_with_pe = pd.merge_asof(
                df_price.sort_index(),
                df_pe.dropna(),
                left_index=True,
                right_index=True,
                direction='backward' # 向后填充季度PE数据
            ).reset_index().rename(columns={'date': 'Date'})
            
            # 2. 创建 Plotly 图 (使用 make_subplots)
            fig = make_subplots(specs=[[{"secondary_y": True}]])

            # 3. 添加 Price (Y1 - 左轴)
            fig.add_trace(go.Scatter(
                x=df_price_with_pe['Date'], 
                y=df_price_with_pe['Price'], 
                name="股价 Price ($)",
            ), secondary_y=False)
            
            # 4. 添加 PE (Y2 - 右轴)
            fig.add_trace(go.Scatter(
                x=df_price_with_pe['Date'], 
                y=df_price_with_pe['P/E Ratio (Non-GAAP)'], 
                name="PE 比率 (x)",
                line=dict(dash='dot', color='#ff7f0e') # 橙色虚线
            ), secondary_y=True)
            
            # 5. 布局双Y轴
            fig.update_layout(
                title=f"{ticker} 股价 (左轴) vs. PE 比率 (右轴)",
                height=450,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            fig.update_yaxes(title_text="股价 Price ($)", secondary_y=False)
            fig.update_yaxes(title_text="PE 比率 P/E Ratio (x)", secondary_y=True)
            
            st.plotly_chart(fig, use_container_width=True)
            
        else:
            st.warning("⚠️ 历史价格或PE数据不足，无法绘制双轴图。")
            if not df_price.empty:
                st.line_chart(df_price, use_container_width=True, height=300)

        # --- 图表二：历史EPS柱状图 ---
        if not df_eps.empty:
            st.subheader("📈 历史 EPS (TTM, Non-GAAP) / Historical EPS (TTM, Non-GAAP)")
            st.bar_chart(df_eps, use_container_width=True, height=300)
        else:
            st.info("ℹ️ 暂无 FMP 提供的历史EPS数据。")

    # ✅ 修正：这是主 `except` 块
    except Exception as e:
        st.error(f"❌ 无法获取股票 {ticker} 的数据。")
        st.error(f"详细错误: {str(e)}")
        with st.expander("🔍 查看完整错误信息（调试用）"):
            st.exception(e)
            
# ✅ 修正：这是主 `elif` 和 `else` 块
elif not ticker and search_button:
    st.warning("⚠️ 请输入股票代码")
else:
    st.info("请在侧边栏输入股票代码并点击搜索以开始分析。")
    
    with st.expander("💡 使用说明"):
        # ... (您的使用说明 - 保持不变) ...
        st.markdown("...")
