import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go

# --- 1. 配置与密钥 ---

st.set_page_config(layout="wide", page_title="股票估值分析", page_icon="🩵")

FMP_API_KEY = st.secrets.get("FMP_API_KEY")

if not FMP_API_KEY:
    st.error("FMP_API_KEY 未在 Streamlit Secrets 中设置！请添加它以便 App 运行。")
    st.info("💡 提示：在 Streamlit Cloud 的 Settings → Secrets 中添加：\n```\nFMP_API_KEY = \"your_api_key_here\"\n```")
    st.stop()

# --- 2. 会话状态初始化 ---

if 'recent_searches' not in st.session_state:
    st.session_state.recent_searches = pd.DataFrame(
        columns=["代码", "公司", "价格", "Trailing PE", "PEG 中枢"]
    )

# 初始化参数存储
if 'current_ticker' not in st.session_state:
    st.session_state.current_ticker = None
if 'g_history' not in st.session_state:
    st.session_state.g_history = 10.0
if 'analyst_weight' not in st.session_state:
    st.session_state.analyst_weight = 0.7

# --- 3. 核心数据获取函数 ---

@st.cache_data(ttl=3600)
def get_stock_data(ticker):
    """
    获取单个股票所需的所有数据 (优化版 - 更可靠的数据源)。
    """
    yf_stock = yf.Ticker(ticker)
    
    # 1. YFinance 基础数据
    yf_info = yf_stock.info
    data = {
        "name": yf_info.get('longName', yf_info.get('shortName', ticker)),
        "price": yf_info.get('currentPrice', yf_info.get('regularMarketPrice', 0)),
        "beta": yf_info.get('beta', 'N/A'),
        "eps_ttm": yf_info.get('trailingEps', 0),
        "eps_fwd": yf_info.get('forwardEps', 0),
        "pe_ttm": yf_info.get('trailingPE', 0),
        "pe_fwd": yf_info.get('forwardPE', 0),
        # 市值数据
        "market_cap": yf_info.get('marketCap', 0),
        "enterprise_value": yf_info.get('enterpriseValue', 0),
        "revenue_ttm": yf_info.get('totalRevenue', 0),
        "profit_margin": yf_info.get('profitMargins', 0),
        "gross_margin": yf_info.get('grossMargins', 0),
        "operating_margin": yf_info.get('operatingMargins', 0),
        # 新增：盈利能力指标
        "roe": yf_info.get('returnOnEquity', 0),
        "roa": yf_info.get('returnOnAssets', 0),
        # 新增：现金流数据
        "free_cash_flow": yf_info.get('freeCashflow', 0),
        "operating_cash_flow": yf_info.get('operatingCashflow', 0),
        # 新增：风险指标
        "debt_to_equity": yf_info.get('debtToEquity', 0),
        "current_ratio": yf_info.get('currentRatio', 0),
        "quick_ratio": yf_info.get('quickRatio', 0),
        # 新增：股息数据
        "dividend_yield": yf_info.get('dividendYield', 0),
        "payout_ratio": yf_info.get('payoutRatio', 0),
        # 新增：其他估值指标
        "price_to_book": yf_info.get('priceToBook', 0),
        "peg_ratio": yf_info.get('pegRatio', 0),
    }
    
    # 计算 P/FCF (市现率)
    if data['free_cash_flow'] > 0 and data['market_cap'] > 0:
        data['p_fcf'] = data['market_cap'] / data['free_cash_flow']
    else:
        data['p_fcf'] = 0
    
    # 2. 获取历史价格数据（5年）
    try:
        hist_price = yf_stock.history(period="5y")
        if not hist_price.empty:
            data["hist_price"] = hist_price['Close']
        else:
            data["hist_price"] = pd.Series()
    except Exception as e:
        data["hist_price"] = pd.Series()
    
    # 3. 计算历史PE（基于价格波动和当前PE）
    # 核心思想：历史PE ≈ (历史价格 / 当前价格) × 当前PE
    try:
        if not data["hist_price"].empty and data.get('pe_ttm') and data['pe_ttm'] > 0 and data['price'] > 0:
            # 按季度重采样
            quarterly_price = data["hist_price"].resample('Q').last()
            
            # 估算历史PE
            hist_pe = (quarterly_price / data['price']) * data['pe_ttm']
            
            # 过滤异常值（PE在5-200之间才合理）
            hist_pe = hist_pe[(hist_pe > 5) & (hist_pe < 200)]
            
            data["hist_pe"] = hist_pe
        else:
            data["hist_pe"] = pd.Series()
    except Exception as e:
        data["hist_pe"] = pd.Series()
    
    # 4. 历史EPS：不计算，直接用空Series（因为反推的EPS不准确）
    data["hist_eps"] = pd.Series()
    
    # 5. 分析师增长率预测（多重备用方案）
    growth_rate = None
    
    # 方案1: 从Forward/Trailing EPS计算（最可靠）
    if data['eps_fwd'] > 0 and data['eps_ttm'] > 0:
        growth_rate = ((data['eps_fwd'] - data['eps_ttm']) / data['eps_ttm']) * 100
    
    # 方案2: 尝试从FMP获取
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
    
    # 方案3: 从YFinance获取行业平均增长率
    if growth_rate is None:
        try:
            # 尝试获取5年EPS增长率
            growth_5y = yf_info.get('earningsQuarterlyGrowth', None)
            if growth_5y:
                growth_rate = growth_5y * 100
        except:
            pass
    
    # 最终默认值
    if growth_rate is None:
        growth_rate = 10.0
    
    # 限制增长率在合理范围内
    growth_rate = max(-50.0, min(growth_rate, 200.0))
    
    data["g_consensus"] = growth_rate
    
    # 6. 获取分析师目标价
    try:
        # YFinance提供分析师目标价数据
        analyst_info = yf_info.get('targetMeanPrice', None)
        analyst_high = yf_info.get('targetHighPrice', None)
        analyst_low = yf_info.get('targetLowPrice', None)
        analyst_median = yf_info.get('targetMedianPrice', None)
        num_analysts = yf_info.get('numberOfAnalystOpinions', None)
        
        data["analyst_target"] = {
            'mean': analyst_info if analyst_info else 0,
            'high': analyst_high if analyst_high else 0,
            'low': analyst_low if analyst_low else 0,
            'median': analyst_median if analyst_median else 0,
            'count': num_analysts if num_analysts else 0
        }
        
        # 尝试从FMP获取更详细的分析师评级
        url_rating = f"https://financialmodelingprep.com/api/v3/rating/{ticker}?apikey={FMP_API_KEY}"
        try:
            rating_response = requests.get(url_rating, timeout=10)
            rating_data = rating_response.json()
            
            if isinstance(rating_data, list) and len(rating_data) > 0:
                latest_rating = rating_data[0]
                data["analyst_rating"] = {
                    'recommendation': latest_rating.get('rating', 'N/A'),
                    'target_price': latest_rating.get('ratingDetailsDCFScore', 0)
                }
            else:
                data["analyst_rating"] = {'recommendation': 'N/A', 'target_price': 0}
        except:
            data["analyst_rating"] = {'recommendation': 'N/A', 'target_price': 0}
            
    except Exception as e:
        data["analyst_target"] = {'mean': 0, 'high': 0, 'low': 0, 'median': 0, 'count': 0}
        data["analyst_rating"] = {'recommendation': 'N/A', 'target_price': 0}
    
    return data

def update_recent_list(ticker, data):
    """更新最近搜索，使用Forward数据"""
    new_entry = {
        "代码 Ticker": ticker.upper(),
        "公司 Company": data['name'][:20] + "..." if len(data['name']) > 20 else data['name'],
        "价格 Price": f"${data['price']:.2f}",
        "Forward PE": f"{data['pe_fwd']:.2f}x" if data.get('pe_fwd') else "N/A",
        "Forward PEG": f"{(data['pe_fwd']/data['g_consensus']):.2f}" if data.get('pe_fwd') and data['g_consensus'] else "N/A"
    }
    # ... 其余代码保持不变
    
    new_df_entry = pd.DataFrame([new_entry])
    
    st.session_state.recent_searches = st.session_state.recent_searches[
        st.session_state.recent_searches['代码'] != ticker.upper()
    ]
    
    st.session_state.recent_searches = pd.concat(
        [new_df_entry, st.session_state.recent_searches],
        ignore_index=True
    ).head(10)

# --- 4. 侧边栏布局 ---

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
    with st.spinner(f"正在获取 {ticker} 的数据..."):
        try:
            data = get_stock_data(ticker)
            
            # --- A. 核心指标 / Core Metrics ---
            st.header(f"📈 {data['name']} ({ticker})")
            
            if data['price'] == 0:
                st.error(f"❌ 无法获取 {ticker} 的有效数据 / Unable to fetch valid data for {ticker}")
                st.stop()
            
            # 第一行：价格和PE指标 / Row 1: Price and PE Metrics
            cols_metrics = st.columns(4)
            cols_metrics[0].metric("💰 当前价格 Current Price", f"${data['price']:.2f}")
            cols_metrics[1].metric("📊 市盈率 P/E (TTM)", f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') and data['pe_ttm'] > 0 else "N/A")
            cols_metrics[2].metric("🔮 远期市盈率 Forward P/E", f"{data['pe_fwd']:.2f}x" if data.get('pe_fwd') and data['pe_fwd'] > 0 else "N/A")
            cols_metrics[3].metric("⚡ 贝塔系数 Beta", f"{data['beta']:.2f}" if isinstance(data.get('beta'), (int, float)) else "N/A")
            
            # 第二行：EPS指标 / Row 2: EPS Metrics
            cols_eps = st.columns(4)
            cols_eps[0].metric("💵 每股收益 EPS (TTM)", f"${data['eps_ttm']:.2f}" if data['eps_ttm'] else "N/A")
            cols_eps[1].metric("🎯 远期EPS Forward EPS", f"${data['eps_fwd']:.2f}" if data['eps_fwd'] else "N/A")
            
            # 计算EPS增长率（如果两者都有）
            if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
                eps_growth = ((data['eps_fwd'] - data['eps_ttm']) / data['eps_ttm']) * 100
                cols_eps[2].metric("📈 EPS增长率 Growth", f"{eps_growth:.1f}%", 
                                  delta=f"{eps_growth:.1f}%",
                                  help="Forward EPS相对TTM EPS的增长")
            else:
                cols_eps[2].metric("📈 EPS增长率 Growth", "N/A")
            
            cols_eps[3].metric("🏦 分析师预期增长 Analyst Growth", f"{data['g_consensus']:.1f}%")
            
            # === 数据污染警告 ===
            st.divider()
            
            # 检测数据污染（Forward EPS 远大于 Trailing EPS）
            if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
                eps_ratio = data['eps_fwd'] / data['eps_ttm']
                
                if eps_ratio > 1.5:  # Forward EPS > Trailing EPS × 150%
                    st.error(f"""
                    ⚠️ **数据警告 Data Quality Warning**
                    
                    该公司的 **Trailing EPS (GAAP)** 受到一次性项目的严重影响：
                    - 📉 Trailing EPS: ${data['eps_ttm']:.2f}
                    - 📈 Forward EPS: ${data['eps_fwd']:.2f} (是Trailing的 **{eps_ratio:.1f}倍**)
                    
                    **可能原因：**
                    - 收购摊销、一次性减值、股权激励等
                    
                    **重要提示：**
                    - ❌ **请完全忽略 Trailing P/E ({data['pe_ttm']:.1f}x)**
                    - ✅ **仅使用 Forward P/E ({data['pe_fwd']:.1f}x)** 进行估值
                    """)
                elif eps_ratio > 1.2:
                    st.warning(f"💡 Forward EPS (${data['eps_fwd']:.2f}) 显著高于 Trailing EPS。建议优先使用 Forward P/E")
            
            # 修正Forward EPS（如果是单季度）
            fwd_eps_display = data['eps_fwd']
            if data['eps_fwd'] and data['eps_ttm'] and data['eps_fwd'] < data['eps_ttm'] * 0.5:
                if data['g_consensus'] and data['g_consensus'] > -30:
                    fwd_eps_display = data['eps_ttm'] * (1 + data['g_consensus']/100)
                    st.info(f"💡 Forward EPS已调整: ${fwd_eps_display:.2f}")
                        
            # 第三行：市值和财务数据 / Row 3: Market Cap and Financial Data
            cols_value = st.columns(4)
            
            # 格式化市值显示
            def format_market_cap(value):
                if value >= 1e12:
                    return f"${value/1e12:.2f}T"
                elif value >= 1e9:
                    return f"${value/1e9:.2f}B"
                elif value >= 1e6:
                    return f"${value/1e6:.2f}M"
                else:
                    return f"${value:,.0f}"
            
            market_cap_str = format_market_cap(data['market_cap']) if data['market_cap'] > 0 else "N/A"
            revenue_str = format_market_cap(data['revenue_ttm']) if data['revenue_ttm'] > 0 else "N/A"
            profit_margin_str = f"{data['profit_margin']*100:.1f}%" if data['profit_margin'] else "N/A"
            
            cols_value[0].metric("🏢 市值 Market Cap", market_cap_str)
            cols_value[1].metric("📊 年营收 Revenue (TTM)", revenue_str)
            cols_value[2].metric("💹 利润率 Profit Margin", profit_margin_str)
            
            # 计算P/FCF
            if data['free_cash_flow'] and data['free_cash_flow'] > 0:
                p_fcf_display = f"{data['p_fcf']:.1f}x"
            else:
                p_fcf_display = "N/A"
            cols_value[3].metric("💸 市现率 P/FCF", p_fcf_display, help="市值/自由现金流")
            
            # 第三行：详细财务指标（可展开）
            with st.expander("📋 查看完整财务数据 View Complete Financial Data"):
                st.markdown("### 💰 盈利能力指标 Profitability (确定值 Definitive)")
                profit_cols = st.columns(4)
                
                roe_str = f"{data['roe']*100:.1f}%" if data['roe'] else "N/A"
                roa_str = f"{data['roa']*100:.1f}%" if data['roa'] else "N/A"
                gross_margin_str = f"{data['gross_margin']*100:.1f}%" if data['gross_margin'] else "N/A"
                operating_margin_str = f"{data['operating_margin']*100:.1f}%" if data['operating_margin'] else "N/A"
                
                profit_cols[0].metric("ROE 净资产收益率", roe_str, help="衡量股东回报效率")
                profit_cols[1].metric("ROA 总资产收益率", roa_str, help="衡量资产使用效率")
                profit_cols[2].metric("Gross Margin 毛利率", gross_margin_str, help="产品定价能力")
                profit_cols[3].metric("Operating Margin 营业利润率", operating_margin_str, help="运营效率")
                
                st.divider()
                st.markdown("### 💸 现金流指标 Cash Flow (确定值 Definitive)")
                cf_cols = st.columns(3)
                
                fcf_str = format_market_cap(data['free_cash_flow']) if data['free_cash_flow'] > 0 else "N/A"
                ocf_str = format_market_cap(data['operating_cash_flow']) if data['operating_cash_flow'] > 0 else "N/A"
                p_fcf_str = f"{data['p_fcf']:.1f}x" if data['p_fcf'] > 0 else "N/A"
                
                cf_cols[0].metric("FCF 自由现金流", fcf_str, help="可分配给股东的现金")
                cf_cols[1].metric("Operating CF 经营现金流", ocf_str, help="核心业务产生的现金")
                cf_cols[2].metric("P/FCF 市现率", p_fcf_str, help="市值/自由现金流，越低越好")
                
                st.divider()
                st.markdown("### ⚖️ 财务健康指标 Financial Health (确定值 Definitive)")
                health_cols = st.columns(3)
                
                de_str = f"{data['debt_to_equity']:.2f}" if data['debt_to_equity'] else "N/A"
                current_str = f"{data['current_ratio']:.2f}" if data['current_ratio'] else "N/A"
                quick_str = f"{data['quick_ratio']:.2f}" if data['quick_ratio'] else "N/A"
                
                health_cols[0].metric("Debt/Equity 债务权益比", de_str, help="<1为健康，>2需警惕")
                health_cols[1].metric("Current Ratio 流动比率", current_str, help=">1.5为良好")
                health_cols[2].metric("Quick Ratio 速动比率", quick_str, help=">1.0为健康")
                
                # 健康度评估
                health_score = 0
                warnings = []
                
                if data['debt_to_equity'] and data['debt_to_equity'] < 1.0:
                    health_score += 1
                elif data['debt_to_equity'] and data['debt_to_equity'] > 2.0:
                    warnings.append("⚠️ 债务水平较高")
                
                if data['current_ratio'] and data['current_ratio'] > 1.5:
                    health_score += 1
                elif data['current_ratio'] and data['current_ratio'] < 1.0:
                    warnings.append("⚠️ 短期偿债能力不足")
                
                if data['free_cash_flow'] and data['free_cash_flow'] > 0:
                    health_score += 1
                else:
                    warnings.append("⚠️ 自由现金流为负")
                
                if health_score >= 2:
                    st.success(f"✅ 财务健康度: 良好 ({health_score}/3)")
                else:
                    st.warning(f"⚠️ 财务健康度: 需关注 ({health_score}/3)")
                    for warning in warnings:
                        st.write(warning)
                
                st.divider()
                st.markdown("### 📈 股息数据 Dividend (确定值 Definitive)")
                div_cols = st.columns(2)
                
                div_yield_str = f"{data['dividend_yield']*100:.2f}%" if data['dividend_yield'] else "无分红 No Dividend"
                payout_str = f"{data['payout_ratio']*100:.1f}%" if data['payout_ratio'] else "N/A"
                
                div_cols[0].metric("Dividend Yield 股息率", div_yield_str)
                div_cols[1].metric("Payout Ratio 分红比率", payout_str, help="分红占净利润比例")
            
            # 第三行：EPS数据（保持原有）
            # cols_eps = st.columns(4)
            # cols_eps[1].metric("💵 Trailing EPS (TTM)", f"${data['eps_ttm']:.2f}" if data['eps_ttm'] else "N/A")
            # cols_eps[2].metric("🎯 Forward EPS (远期)", f"${data['eps_fwd']:.2f}" if data['eps_fwd'] else "N/A")
            
            # 分析师目标价
            if data.get('analyst_target') and data['analyst_target']['mean'] > 0:
                st.divider()
                st.subheader("🎯 分析师目标价")
                
                target_cols = st.columns([1, 2, 1])
                
                with target_cols[1]:
                    analyst_mean = data['analyst_target']['mean']
                    analyst_high = data['analyst_target']['high']
                    analyst_low = data['analyst_target']['low']
                    num_analysts = data['analyst_target']['count']
                    
                    # 显示目标价区间
                    col1, col2, col3 = st.columns(3)
                    col1.metric("📉 最低目标价", f"${analyst_low:.2f}" if analyst_low > 0 else "N/A")
                    col2.metric("🎯 平均目标价", f"${analyst_mean:.2f}")
                    col3.metric("📈 最高目标价", f"${analyst_high:.2f}" if analyst_high > 0 else "N/A")
                    
                    # 上涨空间
                    if analyst_mean > 0:
                        upside = ((analyst_mean - data['price']) / data['price']) * 100
                        
                        if upside > 0:
                            st.success(f"💰 **分析师共识**: 基于 {num_analysts} 位分析师的预测，目标价 ${analyst_mean:.2f}，上涨空间 **+{upside:.1f}%**")
                        else:
                            st.warning(f"⚠️ **分析师共识**: 基于 {num_analysts} 位分析师的预测，目标价 ${analyst_mean:.2f}，下跌风险 **{upside:.1f}%**")
                    
                    # 显示评级（如果有）
                    if data.get('analyst_rating') and data['analyst_rating']['recommendation'] != 'N/A':
                        st.info(f"📊 **最新评级**: {data['analyst_rating']['recommendation']}")
            
            st.divider()

            # --- B. 估值对比：当前价格 vs 合理区间 / Valuation Analysis ---
            st.header("💎 估值分析：当前价格 vs 合理区间 / Valuation Analysis")
            
            # 数据来源说明
            with st.expander("ℹ️ 数据说明 Data Explanation - 重要！"):
                st.markdown("""
                ### 📊 数据分类 Data Classification
                
                #### 1️⃣ 确定值数据 (Definitive Data) - ✅ 事实
                这些数据来自**真实的市场和财报**，在所有平台都一样：
                
                **价格和估值：**
                - 当前价格 Current Price
                - 市值 Market Cap
                - Trailing PE (市盈率 TTM)
                - Forward PE (远期市盈率)
                - Trailing EPS (每股收益 TTM)
                - Forward EPS (远期每股收益)
                
                **财务数据：**
                - 营收 Revenue
                - 利润率 Profit Margin / 毛利率 Gross Margin
                - 自由现金流 FCF
                - 债务比率 Debt-to-Equity
                - 流动比率 Current Ratio
                - ROE, ROA
                - Beta系数
                
                **分析师数据：**
                - 分析师目标价 Analyst Target Price
                - 分析师增长率预测 Analyst Growth Estimates
                
                ---
                
                #### 2️⃣ 估值数据 (Valuation Data) - 📐 计算值
                这些是**本工具基于确定值计算**的估值结果：
                
                | 估值项目 | 计算方法 | 使用的确定值 |
                |---------|---------|-------------|
                | **历史PE** | (历史价格 / 当前价格) × 当前PE | 5年历史价格、当前PE |
                | **历史PE估值区间** | (平均PE ± 标准差) × TTM EPS | 历史PE、TTM EPS |
                | **混合增长率** | 分析师G × 权重 + 历史G × (1-权重) | Forward/Trailing EPS、历史价格CAGR |
                | **PEG估值区间** | (PEG倍数 × 增长率) × TTM EPS | 混合增长率、TTM EPS |
                
                **关键参数：**
                - 历史PE区间：±0.75倍标准差（合理区间），±1.5倍标准差（极端区间）
                - PEG倍数：0.5/0.8/1.0/1.2/2.0（低估到高估）
                - 分析师权重：默认70%（可调整）
                
                ---
                
                #### 3️⃣ 为什么估值会不同？ Why Valuations Differ?
                
                **PE/PEG模型 vs 分析师预测的差异：**
                
                1. **数据基础不同**
                   - PE/PEG：基于历史数据和数学模型，**更保守**
                   - 分析师：综合未来业务、行业趋势、竞争优势等**定性因素**
                
                2. **适用场景不同**
                   - **高成长股**：分析师通常更乐观（看未来潜力）→ 差异大
                   - **成熟股**：模型和分析师较一致 → 差异小
                   - **周期股**：分析师会考虑周期位置 → 可能差异大
                
                3. **时间维度不同**
                   - PE/PEG：主要看过去5年历史
                   - 分析师：主要看未来1-3年预期
                
                **建议：综合参考，关注极端差异**
                - 如果两者接近（±20%以内）→ 估值相对可靠
                - 如果差异巨大（>50%）→ 需要深入研究原因
                """)
            
            # 显示当前价格（大号突出）
            st.markdown(f"### 📍 当前股价 Current Price: **${data['price']:.2f}**")
            st.divider()
            
            # 存储估值结果
            valuation_results = {}
            price_mid_peg = 0.0
            
            # --- B1. 远期P/E估值法（新版） ---
            st.subheader("💰 方法一：远期P/E估值法 / Forward P/E Valuation")
            
            # 计算历史PE统计
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
            
            st.markdown("#### 📊 第一步：PE区间（基于历史统计）")
            
            stat_cols = st.columns(4)
            stat_cols[0].metric("5年平均PE", f"{pe_mean:.1f}x")
            stat_cols[1].metric("标准差", f"{pe_std:.1f}x")
            
            st.info(f"""
            💡 **系统推荐**：
            - 低估PE: {pe_low_rec:.1f}x (均值 - 1σ)
            - 合理PE: {pe_mid_rec:.1f}x (均值)
            - 高估PE: {pe_high_rec:.1f}x (均值 + 1σ)
            
            **您可根据宏观判断调整**（如参考2018年35x）
            """)
            
            st.markdown("#### ⚙️ 第二步：自定义PE区间")

            pe_cols = st.columns(3)
            with pe_cols[0]:
                pe_low = st.number_input(
                    "🟢 低估PE", 
                    min_value=1.0, 
                    value=float(round(pe_low_rec, 1)), 
                    step=1.0,
                    key=f"pe_low_{ticker}"  # ✅ 添加这行
                )
            with pe_cols[1]:
                pe_mid = st.number_input(
                    "🟡 合理PE", 
                    min_value=1.0, 
                    value=float(round(pe_mid_rec, 1)), 
                    step=1.0,
                    key=f"pe_mid_{ticker}"  # ✅ 添加这行
                )
            with pe_cols[2]:
                pe_high = st.number_input(
                    "🔴 高估PE", 
                    min_value=1.0, 
                    value=float(round(pe_high_rec, 1)), 
                    step=1.0,
                    key=f"pe_high_{ticker}"  # ✅ 添加这行
                )
            
            # 使用Forward EPS估值
            if fwd_eps_display and fwd_eps_display > 0:
                price_low = pe_low * fwd_eps_display
                price_mid = pe_mid * fwd_eps_display
                price_high = pe_high * fwd_eps_display
                
                st.markdown("#### 🎯 第三步：估值结果")
                
                result_cols = st.columns(3)
                result_cols[0].metric("🟢 低估价格", f"${price_low:.2f}", 
                                     delta=f"{(price_low/data['price'] - 1)*100:+.1f}%")
                result_cols[1].metric("🟡 合理价格", f"${price_mid:.2f}",
                                     delta=f"{(price_mid/data['price'] - 1)*100:+.1f}%")
                result_cols[2].metric("🔴 高估价格", f"${price_high:.2f}",
                                     delta=f"{(price_high/data['price'] - 1)*100:+.1f}%")
                
                # 估值结论
                if data['price'] < price_low:
                    st.success("🟢 **严重低估** Significantly Undervalued")
                elif data['price'] < price_mid:
                    st.success("🟢 **轻度低估** Moderately Undervalued")
                elif data['price'] < price_high:
                    st.info("🟡 **合理区间** Fair Value Range")
                else:
                    st.warning("🔴 **高估** Overvalued")
                
                # 可视化
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=['低估', '合理', '高估'],
                    y=[price_low, price_mid, price_high],
                    marker_color=['green', 'yellow', 'red'],
                    text=[f'${price_low:.2f}', f'${price_mid:.2f}', f'${price_high:.2f}'],
                    textposition='auto',
                ))
                fig.add_hline(y=data['price'], line_dash="dash", line_color="blue",
                             annotation_text=f"当前 ${data['price']:.2f}")
                fig.update_layout(title="Forward P/E估值区间", yaxis_title="价格 ($)", height=400)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.error("❌ Forward EPS数据缺失")
            
            st.divider()
            
            # -- B2. PEG法 / PEG Valuation Method --
            st.subheader("🚀 方法二：PEG增长估值法 / PEG Growth Valuation")
            
            g_c = data['g_consensus']
            
            # 计算历史增长率（用历史价格CAGR）
            g_h_default = 10.0
            
            if not data['hist_price'].empty:
                try:
                    prices_sorted = data['hist_price'].sort_index()
                    
                    if len(prices_sorted) >= 252:
                        start_price = prices_sorted.iloc[0]
                        end_price = prices_sorted.iloc[-1]
                        start_date = prices_sorted.index[0]
                        end_date = prices_sorted.index[-1]
                        years = (end_date - start_date).days / 365.25
                        
                        if start_price > 0 and end_price > 0 and years > 0:
                            price_cagr = ((end_price / start_price) ** (1 / years) - 1) * 100.0
                            g_h_default = max(-50.0, min(price_cagr, 200.0))
                except Exception as e:
                    g_h_default = 10.0
            
            # 并排展示所有三种增长率计算方法
            st.markdown("#### 📐 增长率计算方法对比 Growth Rate Calculation Methods")
            
            growth_cols = st.columns(3)
            
            # 提前定义这些变量，避免作用域问题
            eps_y0 = data.get('eps_ttm', 0)
            eps_y1 = data.get('eps_fwd', 0)
            roe = data.get('roe', 0)
            payout = data.get('payout_ratio', 0)
            
            # === 方法一：权重平均法 ===
            with growth_cols[0]:
                st.markdown("##### 📊 方法一：权重平均法")
                st.caption("Weighted Average")
                
                with st.container(border=True):
                    # 可调参数
                    g_h_1 = st.number_input("历史增长率%", value=g_h_default, step=0.5, key="g_h_method1", label_visibility="collapsed")
                    weight_1 = st.slider("分析师权重", 0.0, 1.0, 0.7, 0.05, key="weight_method1", label_visibility="collapsed")
                    
                    g_method1 = (g_c * weight_1) + (g_h_1 * (1 - weight_1))
                    
                    st.metric("混合增长率", f"{g_method1:.1f}%", help=f"分析师{g_c:.1f}% × {weight_1:.0%} + 历史{g_h_1:.1f}% × {1-weight_1:.0%}")
                    
                    st.caption(f"📝 公式: ({g_c:.1f}% × {weight_1:.1%}) + ({g_h_1:.1f}% × {1-weight_1:.1%})")
                    st.caption("✅ 适用: 大多数公司")
            
            # === 方法二：基本面/可持续增长率 ===
            with growth_cols[1]:
                st.markdown("##### 💰 方法二：可持续增长")
                st.caption("Sustainable Growth (ROE)")
                
                with st.container(border=True):
                    st.metric("ROE", f"{roe*100:.1f}%" if roe else "N/A", help="净资产收益率")
                    st.metric("Payout", f"{payout*100:.1f}%" if payout else "30% (假设)")
                    
                    if roe and roe > 0:
                        actual_payout = payout if payout else 0.3
                        g_method2 = max(0, min(roe * (1 - actual_payout) * 100, 100))
                        
                        st.metric("可持续增长率", f"{g_method2:.1f}%", help=f"ROE × (1 - Payout)")
                        st.caption(f"📝 公式: {roe*100:.1f}% × (1 - {actual_payout*100:.0f}%)")
                    else:
                        g_method2 = None
                        st.error("ROE数据缺失")
                    
                    st.caption("✅ 适用: 成熟稳定企业")
            
            # === 方法三：多阶段增长率 ===
            with growth_cols[2]:
                st.markdown("##### 🚀 方法三：多阶段增长")
                st.caption("Multi-Stage Growth")
                
                with st.container(border=True):
                    st.metric("TTM EPS", f"${eps_y0:.2f}" if eps_y0 else "N/A")
                    st.metric("Fwd EPS", f"${eps_y1:.2f}" if eps_y1 else "N/A")
                    
                    if eps_y0 and eps_y1 and eps_y0 > 0:
                        g_y1 = ((eps_y1 - eps_y0) / eps_y0) * 100
                        g_y3_5 = g_c  # 使用分析师共识作为Y3-5增长率
                        
                        eps_y5 = eps_y1 * ((1 + g_y3_5/100) ** 4)
                        g_method3 = max(0, min(((eps_y5 / eps_y0) ** (1/5) - 1) * 100, 100))
                        
                        st.metric("5年CAGR", f"{g_method3:.1f}%", help="考虑短期+中期增长")
                        st.caption(f"📝 Y1: {g_y1:.0f}%, Y3-5: {g_y3_5:.0f}%")
                    else:
                        g_method3 = None
                        st.error("EPS数据不足")
                    
                    st.caption("✅ 适用: 高成长股")
            
            # === 智能推荐最佳方法 ===
            st.divider()
            st.markdown("#### 🎯 智能推荐 Smart Recommendation")
            
            # 分析公司特征
            revenue = data.get('revenue_ttm', 0)
            market_cap = data.get('market_cap', 0)
            roe_val = data.get('roe', 0)
            
            # 判断公司类型
            is_large_cap = market_cap > 200e9  # >$200B
            is_mature = roe_val and roe_val > 0 and roe_val < 0.25 and g_h_default < 15
            is_high_growth = g_h_default > 20 or (eps_y1 and eps_y0 and eps_y1 > eps_y0 * 1.15)
            
            # 推荐逻辑
            recommendations = []
            
            if g_method3 is not None and is_high_growth:
                recommended_method = "方法三"
                recommended_growth = g_method3
                reason = f"高成长股（历史增长{g_h_default:.0f}%），短期加速明显"
            elif g_method2 is not None and is_mature and is_large_cap:
                recommended_method = "方法二"
                recommended_growth = g_method2
                reason = f"成熟大盘股（市值{market_cap/1e9:.0f}B），ROE稳定"
            else:
                recommended_method = "方法一"
                recommended_growth = g_method1
                reason = "平衡方法，适合大多数情况"
            
            rec_cols = st.columns([2, 1, 2])
            
            with rec_cols[0]:
                st.info(f"**推荐使用**: {recommended_method}")
                st.caption(f"原因: {reason}")
            
            with rec_cols[1]:
                st.metric("📊 推荐增长率", f"{recommended_growth:.1f}%", 
                         delta=f"vs分析师 {recommended_growth - g_c:+.1f}%")
            
            with rec_cols[2]:
                # 显示所有方法的对比
                g2_display = f"{g_method2:.1f}%" if g_method2 else "N/A"
                g2_diff = f"{g_method2 - g_c:+.1f}%" if g_method2 else "N/A"
                g3_display = f"{g_method3:.1f}%" if g_method3 else "N/A"
                g3_diff = f"{g_method3 - g_c:+.1f}%" if g_method3 else "N/A"
                
                comparison_df = pd.DataFrame({
                    "方法": ["方法一", "方法二", "方法三"],
                    "增长率": [f"{g_method1:.1f}%", g2_display, g3_display],
                    "vs分析师": [f"{g_method1 - g_c:+.1f}%", g2_diff, g3_diff]
                })
                
                st.dataframe(comparison_df, hide_index=True, use_container_width=True)
            
            # 使用推荐的增长率进行后续估值
            # 使用推荐的增长率进行PEG比率分析
            # 使用推荐的增长率进行PEG比率分析
            g_blended = recommended_growth
            
            st.markdown("#### 🎯 远期PEG比率计算")
            
            st.info("💡 PEG不再计算价格，而是分析当前估值合理性")
            
            if g_blended > 0 and data['pe_fwd'] and data['pe_fwd'] > 0:
                # 计算Forward PEG
                forward_peg = data['pe_fwd'] / g_blended
                
                peg_cols = st.columns(3)
                peg_cols[0].metric("📊 Forward P/E", f"{data['pe_fwd']:.2f}x")
                peg_cols[1].metric("📈 混合增长率", f"{g_blended:.1f}%")
                peg_cols[2].metric("⭐ Forward PEG", f"{forward_peg:.2f}x")
                
                st.markdown("---")
                st.markdown("#### 📋 PEG估值判断")
                
                guide_cols = st.columns(5)
                guide_cols[0].metric("🟢🟢 极度低估", "< 0.5")
                guide_cols[1].metric("🟢 低估", "0.5-0.8")
                guide_cols[2].metric("🟡 合理", "0.8-1.2")
                guide_cols[3].metric("🔴 高估", "1.2-2.0")
                guide_cols[4].metric("🔴🔴 严重高估", "> 2.0")
                
                # 判断
                if forward_peg < 0.5:
                    st.success(f"✅ **极度低估** Forward PEG = {forward_peg:.2f}x")
                elif forward_peg < 0.8:
                    st.success(f"✅ **低估** Forward PEG = {forward_peg:.2f}x")
                elif forward_peg <= 1.2:
                    st.info(f"💡 **合理** Forward PEG = {forward_peg:.2f}x")
                elif forward_peg <= 2.0:
                    st.warning(f"⚠️ **高估** Forward PEG = {forward_peg:.2f}x")
                else:
                    st.error(f"❌ **严重高估** Forward PEG = {forward_peg:.2f}x")
            else:
                st.error("⚠️ 数据不足")
            
            st.divider()
            
            st.divider()

            # -- B3. 分析师目标价 --
            st.subheader("🏦 方法三：分析师目标价 / Analyst Targets")
            
            analyst_target = data.get('analyst_target', {})
            if analyst_target and analyst_target.get('mean', 0) > 0:
                analyst_mean = analyst_target['mean']
                analyst_low = analyst_target.get('low', 0)
                analyst_high = analyst_target.get('high', 0)
                num_analysts = analyst_target.get('count', 0)
                
                analyst_cols = st.columns(4)
                analyst_cols[0].metric("🎯 均值", f"${analyst_mean:.2f}",
                                      delta=f"{(analyst_mean/data['price'] - 1)*100:+.1f}%")
                analyst_cols[1].metric("🔻 最低", f"${analyst_low:.2f}" if analyst_low > 0 else "N/A")
                analyst_cols[2].metric("🔺 最高", f"${analyst_high:.2f}" if analyst_high > 0 else "N/A")
                analyst_cols[3].metric("👥 分析师", f"{num_analysts}")
                
                # 与Forward PE对比
                if price_mid > 0:
                    diff = ((analyst_mean - price_mid) / price_mid * 100)
                    if abs(diff) < 10:
                        st.success(f"✅ 与Forward PE估值一致 (差异{abs(diff):.1f}%)")
                    elif diff > 0:
                        st.info(f"📊 分析师更乐观 (+{diff:.1f}%)")
                    else:
                        st.warning(f"📊 分析师更谨慎 ({diff:.1f}%)")
            else:
                st.info("💡 暂无分析师数据")
            
        

            # --- C. 历史图表 / Historical Charts ---
            st.divider()
            st.header("📊 历史发展过程 (5年) / 5-Year Historical Performance")
            
            # 合并图表：股价（线图）+ PE（柱图）双Y轴
            if not data['hist_price'].empty and not data['hist_pe'].empty:
                st.subheader("💹 股价 vs PE 历史对比 / Price vs P/E History")
                
                # 准备数据
                df_price = data['hist_price'].to_frame('股价 Price')
                df_pe = data['hist_pe'].to_frame('PE比率 P/E Ratio')
                
                # 按季度重采样PE数据以匹配
                df_pe_resampled = df_pe.resample('Q').last().reindex(df_price.index, method='ffill')
                
                # 合并数据
                df_combined = df_price.join(df_pe_resampled, how='left')
                
                # 计算统计信息
                price_change = ((data['price'] - data['hist_price'].iloc[0]) / data['hist_price'].iloc[0] * 100)
                pe_mean = data['hist_pe'].mean()
                pe_current = data['pe_ttm']
                pe_std = data['hist_pe'].std()
                
                # 显示关键指标
                stat_cols = st.columns(4)
                stat_cols[0].metric("📈 5年涨幅 5Y Return", f"{price_change:.1f}%")
                stat_cols[1].metric("📊 平均PE Avg P/E", f"{pe_mean:.1f}x")
                stat_cols[2].metric("📍 当前PE Current P/E", f"{pe_current:.1f}x")
                
                # PE位置判断
                pe_position = (pe_current - pe_mean) / pe_std if pe_std > 0 else 0
                if pe_position < -0.75:
                    pe_status = "极低 Very Low"
                    pe_color = "🟢"
                elif pe_position < 0:
                    pe_status = "偏低 Low"
                    pe_color = "🟢"
                elif pe_position < 0.75:
                    pe_status = "偏高 High"  
                    pe_color = "🟡"
                else:
                    pe_status = "极高 Very High"
                    pe_color = "🔴"
                
                stat_cols[3].metric("📏 PE位置 P/E Position", f"{pe_color} {pe_status}", 
                                  help=f"标准差: {pe_position:.1f}σ")
                
                # 创建两个独立的图表以实现不同Y轴
                chart_col1, chart_col2 = st.columns([3, 2])
                
                with chart_col1:
                    st.caption("📈 双Y轴图：蓝线=股价(左轴), 蓝柱=PE(右轴)")
                    
                    # 使用Streamlit的原生图表（简化版）
                    # 注意：Streamlit原生不支持真正的双Y轴，我们用两个图叠加
                    st.line_chart(df_combined, height=400)
                    
                with chart_col2:
                    st.markdown("#### 📊 PE区间分析 P/E Analysis")
                    
                    pe_low = pe_mean - pe_std
                    pe_high = pe_mean + pe_std
                    
                    st.write(f"**历史区间 Historical Range:**")
                    st.write(f"- 🟢 低估区 Low: < {pe_low:.1f}x")
                    st.write(f"- 🟡 合理区 Fair: {pe_low:.1f}x - {pe_high:.1f}x")
                    st.write(f"- 🔴 高估区 High: > {pe_high:.1f}x")
                    st.write(f"- 📍 当前 Current: **{pe_current:.1f}x**")
                    
                    # 判断当前位置
                    if pe_current < pe_low:
                        st.success("✅ PE处于历史低位 P/E at historical low")
                    elif pe_current < pe_high:
                        st.info("💡 PE处于合理区间 P/E in fair range")
                    else:
                        st.warning("⚠️ PE处于历史高位 P/E at historical high")
                        
                    # 添加PE趋势说明
                    st.divider()
                    st.caption("💡 **解读 Interpretation:**")
                    st.caption("- PE上升 + 股价上升 = 估值扩张")
                    st.caption("- PE下降 + 股价上升 = 盈利驱动")
                    st.caption("- PE下降 + 股价下降 = 估值收缩")
            else:
                # 单独显示可用的图表
                chart_cols = st.columns(2)
                
                with chart_cols[0]:
                    if not data['hist_price'].empty:
                        st.subheader("💹 股价走势 Price History")
                        price_change = ((data['price'] - data['hist_price'].iloc[0]) / data['hist_price'].iloc[0] * 100)
                        st.caption(f"5年涨幅: {price_change:.1f}%")
                        st.line_chart(data['hist_price'], height=300)
                
                with chart_cols[1]:
                    if not data['hist_pe'].empty:
                        st.subheader("📈 历史PE比率 P/E History")
                        pe_mean = data['hist_pe'].mean()
                        pe_current = data['pe_ttm']
                        st.caption(f"平均: {pe_mean:.1f}x | 当前: {pe_current:.1f}x")
                        st.line_chart(data['hist_pe'], height=300)
            
            # 估值区间可视化对比 / Valuation Range Visualization
            if len(valuation_results) > 0:
                st.divider()
                st.subheader("🎯 估值区间可视化对比 / Valuation Range Comparison")
                
                # 创建更直观的区间展示
                for method_key, vals in valuation_results.items():
                    st.markdown(f"**{vals['method']} / {method_key.upper()} Method**")
                    
                    # 创建价格标尺
                    min_price = vals['very_low']
                    max_price = vals['very_high']
                    current_price = data['price']
                    
                    # 计算当前价格的位置百分比
                    if max_price > min_price:
                        position_pct = ((current_price - min_price) / (max_price - min_price)) * 100
                        position_pct = max(0, min(position_pct, 100))
                    else:
                        position_pct = 50
                    
                    # 创建可视化区间
                    cols = st.columns([1, 3, 1])
                    
                    with cols[0]:
                        st.metric("极低", f"${vals['very_low']:.0f}")
                    
                    with cols[1]:
                        # 使用进度条展示价格位置
                        st.markdown(f"<div style='background: linear-gradient(to right, #00ff00 0%, #00ff00 20%, #90EE90 20%, #90EE90 40%, #FFD700 40%, #FFD700 60%, #FFA500 60%, #FFA500 80%, #ff0000 80%, #ff0000 100%); height: 30px; border-radius: 5px; position: relative;'>"
                                   f"<div style='position: absolute; left: {position_pct}%; top: 0; width: 3px; height: 30px; background: black;'></div>"
                                   f"<div style='position: absolute; left: {position_pct}%; top: -25px; font-weight: bold; color: black;'>↓ 当前${current_price:.0f}</div>"
                                   f"</div>", unsafe_allow_html=True)
                        
                        # 区间标签
                        label_cols = st.columns(5)
                        label_cols[0].caption("🔻🔻 极低估")
                        label_cols[1].caption("🔻 低估")
                        label_cols[2].caption("🎯 合理")
                        label_cols[3].caption("🔺 高估")
                        label_cols[4].caption("🔺🔺 极高估")
                    
                    with cols[2]:
                        st.metric("极高", f"${vals['very_high']:.0f}")
                    
                    st.divider()
                
                # 添加分析师目标价对比
                if analyst_mean > 0:
                    st.markdown("**🏦 分析师目标价 / Analyst Target Price**")
                    avg_mid = sum([v['mid'] for v in valuation_results.values()]) / len(valuation_results)
                    
                    compare_cols = st.columns(3)
                    compare_cols[0].metric("模型合理价 Model Fair", f"${avg_mid:.2f}")
                    compare_cols[1].metric("分析师目标 Analyst", f"${analyst_mean:.2f}")
                    compare_cols[2].metric("差异 Difference", f"{((analyst_mean - avg_mid) / avg_mid * 100):.1f}%")
                    
                    # 解释差异原因
                    st.info("""
                    **💡 为什么估值会有差异？ Why Valuation Differences?**
                    
                    1. **PE/PEG模型 Model**: 基于历史数据和增长率的数学计算，更保守
                       - Based on historical data and growth rates, more conservative
                    
                    2. **分析师预测 Analyst**: 综合考虑未来业务、行业趋势、竞争优势等定性因素
                       - Consider future business, industry trends, competitive advantages
                    
                    3. **常见差异 Common Gaps**:
                       - 高成长股：分析师通常更乐观（看未来潜力）
                       - 成熟股：模型和分析师较一致
                       - 周期股：分析师考虑周期位置
                    
                    **建议 Recommendation**: 综合参考两种方法，关注极端差异情况
                    """)

        except Exception as e:
            st.error(f"❌ 无法获取股票 {ticker} 的数据。")
            st.error(f"详细错误: {str(e)}")
            with st.expander("🔍 查看完整错误信息（调试用）"):
                st.exception(e)
            
elif not ticker and search_button:
    st.warning("⚠️ 请输入股票代码")
else:
    st.info("请在侧边栏输入股票代码并点击搜索以开始分析。")
    
    with st.expander("💡 使用说明"):
        st.markdown("""
        ### 如何使用估值分析器
        
        1. **输入股票代码**: 在左侧输入框输入美股代码（如 AAPL, NVDA, TSLA）
        2. **查看估值区间**: 
           - **历史PE法**: 基于过去5年的PE比率波动
           - **PEG估值法**: 基于未来增长预期
        3. **调整参数**: 可以调整分析师预测的权重
        4. **查看历史**: 下方图表显示5年的价格、PE、EPS走势
        
        ### 关键指标说明
        
        - **Trailing PE**: 过去12个月的市盈率
        - **Forward PE**: 基于未来预期的市盈率
        - **PEG**: PE除以增长率，通常<1表示估值合理
        - **Beta**: 相对大盘的波动性，1.0表示与大盘同步
        
        ### 估值可靠性
        
        - ✅ **绿色**: 估值合理或被低估
        - ⚠️ **黄色**: 略微高估，需要关注
        - ❌ **红色**: 明显高估，需要谨慎
        """)
