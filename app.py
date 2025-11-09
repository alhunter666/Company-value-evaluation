import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import numpy as np

# --- 1. 配置与密钥 ---

st.set_page_config(layout="wide", page_title="股票估值分析 Equity Valuation Analysis", page_icon="🩵")

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

def update_recent_list(ticker, data, price_mid_peg):
    """
    更新侧边栏的最近10条搜索记录。
    """
    new_entry = {
        "代码": ticker.upper(),
        "公司": data['name'][:20] + "..." if len(data['name']) > 20 else data['name'],
        "价格": f"${data['price']:.2f}",
        "Trailing PE": f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') and data['pe_ttm'] > 0 else "N/A",
        "PEG 中枢": f"${price_mid_peg:.2f}" if price_mid_peg > 0 else "N/A"
    }
    
    new_df_entry = pd.DataFrame([new_entry])
    
    st.session_state.recent_searches = st.session_state.recent_searches[
        st.session_state.recent_searches['代码'] != ticker.upper()
    ]
    
    st.session_state.recent_searches = pd.concat(
        [new_df_entry, st.session_state.recent_searches],
        ignore_index=True
    ).head(10)

# --- 4. 侧边栏布局 ---

st.sidebar.title("📊 估值分析器")
st.sidebar.caption("使用历史PE法与PEG法进行估值")

ticker = st.sidebar.text_input("输入股票代码 (e.g., AAPL, NVDA)", key="ticker_input").strip().upper()
search_button = st.sidebar.button("🔍 搜索", use_container_width=True, type="primary")

st.sidebar.divider()
st.sidebar.subheader("📋 最近10次搜索")

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
            
            # -- B1. 历史PE法 / Historical P/E Method --
            st.subheader("📊 方法一：历史PE估值法 / Historical P/E Valuation")
            hist_pe = data['hist_pe'].dropna() if not data['hist_pe'].empty else pd.Series()
            
            if not hist_pe.empty and len(hist_pe) >= 4 and data['eps_ttm'] and data['eps_ttm'] > 0:
                p_mean = hist_pe.mean()
                p_std = hist_pe.std()
                
                # 优化：使用更保守的区间 (±0.75倍标准差作为合理区间，±1.5倍作为极端区间)
                price_very_low = max(0, (p_mean - 1.5 * p_std)) * data['eps_ttm']
                price_low_hist = max(0, (p_mean - 0.75 * p_std)) * data['eps_ttm']
                price_mid_hist = p_mean * data['eps_ttm']
                price_high_hist = (p_mean + 0.75 * p_std) * data['eps_ttm']
                price_very_high = (p_mean + 1.5 * p_std) * data['eps_ttm']
                
                valuation_results['hist_pe'] = {
                    'very_low': price_very_low,
                    'low': price_low_hist,
                    'mid': price_mid_hist,
                    'high': price_high_hist,
                    'very_high': price_very_high,
                    'method': '历史PE法'
                }
                
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("🔻🔻 极度低估 Deep Value", f"${price_very_low:.2f}", help=f"PE: {(p_mean - 1.5 * p_std):.1f}x")
                col2.metric("🔻 低估区间 Undervalued", f"${price_low_hist:.2f}", help=f"PE: {(p_mean - 0.75 * p_std):.1f}x")
                col3.metric("🎯 合理中枢 Fair Value", f"${price_mid_hist:.2f}", help=f"PE: {p_mean:.1f}x")
                col4.metric("🔺 高估区间 Overvalued", f"${price_high_hist:.2f}", help=f"PE: {(p_mean + 0.75 * p_std):.1f}x")
                col5.metric("🔺🔺 严重高估 Extreme", f"${price_very_high:.2f}", help=f"PE: {(p_mean + 1.5 * p_std):.1f}x")
                
                # 评估建议
                if data['price'] < price_very_low:
                    st.error(f"⚠️ **异常低价 Abnormal**: 当前价格可能存在基本面问题，需要深入研究")
                elif data['price'] < price_low_hist:
                    discount_pct = ((price_mid_hist - data['price']) / price_mid_hist * 100)
                    st.success(f"✅ **买入机会 Strong Buy**: 相对合理价有 **{discount_pct:.1f}%** 上涨空间")
                elif data['price'] <= price_mid_hist:
                    st.success(f"✅ **合理偏低 Fair-Low**: 估值合理偏低，可以考虑买入")
                elif data['price'] <= price_high_hist:
                    st.info(f"💡 **合理偏高 Fair-High**: 估值略高但在合理区间")
                elif data['price'] <= price_very_high:
                    over_pct = ((data['price'] - price_mid_hist) / price_mid_hist * 100)
                    st.warning(f"⚠️ **高估 Overvalued**: 相对合理价高估 **{over_pct:.1f}%**")
                else:
                    st.error(f"❌ **严重高估 Severely Overvalued**: 价格远超历史合理区间")
                
                with st.expander("📈 查看计算详情 View Details"):
                    st.write(f"- 历史平均PE Historical Avg P/E: {p_mean:.2f}x")
                    st.write(f"- 历史标准差 Std Dev: {p_std:.2f}x")
                    st.write(f"- TTM EPS: ${data['eps_ttm']:.2f}")
                    st.write(f"- 合理PE区间 Fair P/E Range: {(p_mean - 0.75 * p_std):.1f}x - {(p_mean + 0.75 * p_std):.1f}x")
            else:
                st.warning("⚠️ 历史PE数据不足 Insufficient historical P/E data")
            
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
            
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                g_h = st.number_input("📊 历史增长率 % Historical Growth", value=g_h_default, step=0.5, key="g_history_input")
            with col_g2:
                weight = st.slider("⚖️ 分析师权重 Analyst Weight", 0.0, 1.0, 0.7, 0.05, key="g_weight_slider")
            
            g_blended = (g_c * weight) + (g_h * (1 - weight))
            st.info(f"🔄 混合增长率 Blended Growth: 分析师 {g_c:.1f}% × {weight:.0%} + 历史 {g_h:.1f}% × {1-weight:.0%} = **{g_blended:.2f}%**")
            
            if g_blended > 0 and data['pe_ttm'] and data['pe_ttm'] > 0 and data['eps_ttm'] and data['eps_ttm'] > 0:
                # 优化：使用更精细的PEG区间
                # PEG < 0.5: 极度低估
                # PEG 0.5-0.8: 低估
                # PEG 0.8-1.2: 合理
                # PEG 1.2-2.0: 高估
                # PEG > 2.0: 严重高估
                
                price_very_low_peg = 0.5 * g_blended * data['eps_ttm']
                price_low_peg = 0.8 * g_blended * data['eps_ttm']
                price_mid_peg = 1.0 * g_blended * data['eps_ttm']
                price_high_peg = 1.2 * g_blended * data['eps_ttm']
                price_very_high_peg = 2.0 * g_blended * data['eps_ttm']
                
                valuation_results['peg'] = {
                    'very_low': price_very_low_peg,
                    'low': price_low_peg,
                    'mid': price_mid_peg,
                    'high': price_high_peg,
                    'very_high': price_very_high_peg,
                    'method': 'PEG法'
                }
                
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("🔻🔻 极度低估", f"${price_very_low_peg:.2f}", help="PEG = 0.5")
                col2.metric("🔻 保守估值", f"${price_low_peg:.2f}", help="PEG = 0.8")
                col3.metric("🎯 合理估值", f"${price_mid_peg:.2f}", help="PEG = 1.0")
                col4.metric("🔺 偏高估值", f"${price_high_peg:.2f}", help="PEG = 1.2")
                col5.metric("🔺🔺 严重高估", f"${price_very_high_peg:.2f}", help="PEG = 2.0")
                
                # 当前PEG
                current_peg = data['pe_ttm'] / g_blended
                st.metric("📊 当前PEG比率 Current PEG", f"{current_peg:.2f}")
                
                # 评估建议
                if current_peg < 0.5:
                    st.success(f"✅ **极度低估 Deep Value**: PEG = {current_peg:.2f}，增长潜力被严重低估")
                elif current_peg < 0.8:
                    st.success(f"✅ **强烈买入 Strong Buy**: PEG = {current_peg:.2f}，估值吸引")
                elif current_peg <= 1.2:
                    st.info(f"💡 **合理估值 Fair Value**: PEG = {current_peg:.2f}，估值合理")
                elif current_peg <= 2.0:
                    st.warning(f"⚠️ **偏高估值 Overvalued**: PEG = {current_peg:.2f}，增长预期较高")
                else:
                    st.error(f"❌ **严重高估 Severely Overvalued**: PEG = {current_peg:.2f}，增长预期过高")
                
                with st.expander("🔍 查看计算详情 View Details"):
                    st.write(f"- 当前PE Current P/E: {data['pe_ttm']:.2f}x")
                    st.write(f"- 混合增长率 Blended Growth: {g_blended:.2f}%")
                    st.write(f"- 当前PEG Current PEG: {current_peg:.2f}")
                    st.write(f"- TTM EPS: ${data['eps_ttm']:.2f}")
                    st.write(f"\n**PEG估值标准 PEG Valuation Guide:**")
                    st.write(f"- PEG < 0.5: 极度低估 Deep Value")
                    st.write(f"- PEG 0.5-0.8: 低估 Undervalued")
                    st.write(f"- PEG 0.8-1.2: 合理 Fair")
                    st.write(f"- PEG 1.2-2.0: 高估 Overvalued")
                    st.write(f"- PEG > 2.0: 严重高估 Severely Overvalued")
            else:
                st.error("⚠️ 增长率为负或数据不足 Negative growth or insufficient data")
            
            st.divider()
            
            # -- B3. 综合建议 --
            if len(valuation_results) >= 1:
                st.subheader("🎯 综合估值建议")
                
                # 计算平均估值区间
                all_lows = [v['low'] for v in valuation_results.values()]
                all_mids = [v['mid'] for v in valuation_results.values()]
                all_highs = [v['high'] for v in valuation_results.values()]
                
                avg_low = sum(all_lows) / len(all_lows)
                avg_mid = sum(all_mids) / len(all_mids)
                avg_high = sum(all_highs) / len(all_highs)
                
                # 添加分析师目标价（如果有）
                analyst_mean = data.get('analyst_target', {}).get('mean', 0)
                num_cols = 5 if analyst_mean > 0 else 4
                
                cols = st.columns(num_cols)
                cols[0].metric("📍 当前价格", f"${data['price']:.2f}")
                cols[1].metric("🔻 综合低估区", f"${avg_low:.2f}")
                cols[2].metric("🎯 综合合理价", f"${avg_mid:.2f}")
                cols[3].metric("🔺 综合高估区", f"${avg_high:.2f}")
                
                if analyst_mean > 0:
                    cols[4].metric("🏦 分析师目标", f"${analyst_mean:.2f}")
                
                # 最终建议
                if data['price'] < avg_low:
                    upside = ((avg_mid - data['price']) / data['price'] * 100)
                    st.success(f"### 💰 **投资建议: 买入** \n当前价格被低估，上涨空间约 **{upside:.1f}%** 至合理价位。")
                elif data['price'] < avg_mid:
                    upside = ((avg_mid - data['price']) / data['price'] * 100)
                    st.success(f"### ✅ **投资建议: 可以买入** \n当前价格合理偏低，仍有 **{upside:.1f}%** 上涨空间。")
                elif data['price'] < avg_high:
                    st.info(f"### 💡 **投资建议: 持有** \n当前价格在合理区间内，建议持有观望。")
                else:
                    downside = ((data['price'] - avg_mid) / data['price'] * 100)
                    st.warning(f"### ⚠️ **投资建议: 考虑减仓** \n当前价格被高估约 **{downside:.1f}%**，建议等待回调。")
                
                # 对比分析师目标价
                if analyst_mean > 0:
                    analyst_vs_model = ((analyst_mean - avg_mid) / avg_mid * 100)
                    if abs(analyst_vs_model) < 10:
                        st.success(f"✅ **估值一致性**: 分析师目标价 (${analyst_mean:.2f}) 与模型估值基本一致，相差 {abs(analyst_vs_model):.1f}%")
                    elif analyst_mean > avg_mid:
                        st.info(f"📊 **估值对比**: 分析师目标价 (${analyst_mean:.2f}) 比模型估值高 {analyst_vs_model:.1f}%，市场预期更乐观")
                    else:
                        st.warning(f"📊 **估值对比**: 分析师目标价 (${analyst_mean:.2f}) 比模型估值低 {abs(analyst_vs_model):.1f}%，市场预期更谨慎")
            
            update_recent_list(ticker, data, price_mid_peg)

            # --- C. 历史图表 ---
            st.divider()
            st.header("📊 历史发展过程 (5年)")
            
            # 合并图表：股价 + PE 双轴
            if not data['hist_price'].empty and not data['hist_pe'].empty:
                st.subheader("💹 股价 vs PE 历史对比")
                
                # 准备数据
                df_combined = pd.DataFrame({
                    '股价': data['hist_price']
                })
                
                # 将季度PE数据对齐到每日
                df_combined = df_combined.join(data['hist_pe'].rename('PE比率'), how='left')
                df_combined['PE比率'] = df_combined['PE比率'].fillna(method='ffill')  # 向前填充
                
                # 计算统计信息
                price_change = ((data['price'] - data['hist_price'].iloc[0]) / data['hist_price'].iloc[0] * 100)
                pe_mean = data['hist_pe'].mean()
                pe_current = data['pe_ttm']
                
                # 显示关键指标
                stat_cols = st.columns(4)
                stat_cols[0].metric("📈 5年涨幅", f"{price_change:.1f}%")
                stat_cols[1].metric("📊 平均PE", f"{pe_mean:.1f}x")
                stat_cols[2].metric("📍 当前PE", f"{pe_current:.1f}x")
                stat_cols[3].metric("📏 PE位置", f"{((pe_current - pe_mean) / pe_mean * 100):.0f}%", 
                                  help="当前PE相对于历史平均的位置")
                
                # 创建双Y轴图表
                col_chart1, col_chart2 = st.columns([2, 1])
                
                with col_chart1:
                    st.line_chart(df_combined, height=350)
                    st.caption("💡 提示: 股价和PE通常呈正相关，但PE过高可能意味着估值过贵")
                
                with col_chart2:
                    st.markdown("#### 📊 PE 分析")
                    
                    # PE区间分析
                    pe_std = data['hist_pe'].std()
                    pe_low = pe_mean - pe_std
                    pe_high = pe_mean + pe_std
                    
                    st.write(f"**历史区间分析:**")
                    st.write(f"- 低估区: {pe_low:.1f}x 以下")
                    st.write(f"- 合理区: {pe_low:.1f}x - {pe_high:.1f}x")
                    st.write(f"- 高估区: {pe_high:.1f}x 以上")
                    st.write(f"- 当前PE: **{pe_current:.1f}x**")
                    
                    # 判断当前位置
                    if pe_current < pe_low:
                        st.success("✅ PE处于历史低位")
                    elif pe_current < pe_high:
                        st.info("💡 PE处于合理区间")
                    else:
                        st.warning("⚠️ PE处于历史高位")
            else:
                # 单独显示可用的图表
                if not data['hist_price'].empty:
                    st.subheader("💹 股价走势")
                    price_change = ((data['price'] - data['hist_price'].iloc[0]) / data['hist_price'].iloc[0] * 100)
                    st.caption(f"5年涨幅: {price_change:.1f}%")
                    st.line_chart(data['hist_price'], height=300)
                
                if not data['hist_pe'].empty:
                    st.subheader("📈 历史 PE 比率")
                    pe_mean = data['hist_pe'].mean()
                    pe_current = data['pe_ttm']
                    st.caption(f"5年平均PE: {pe_mean:.1f}x | 当前PE: {pe_current:.1f}x")
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
