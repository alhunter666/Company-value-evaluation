import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import time  # 用于处理频率限制

# --- 1. 配置与密钥 ---

st.set_page_config(layout="wide", page_title="股票估值分析工具", page_icon="🩵")

# 尝试获取 API Key
try:
    FMP_API_KEY = st.secrets.get("FMP_API_KEY")
except FileNotFoundError:
    FMP_API_KEY = None

# 如果没有 Key，在侧边栏提示输入
if not FMP_API_KEY:
    with st.sidebar:
        with st.expander("⚙️ API 设置"):
            st.warning("⚠️ 未检测到 FMP API Key")
            FMP_API_KEY = st.text_input("输入 FMP Key (可选)", type="password", help="用于获取更详细的分析师评级数据。如果没有，基础功能仍可使用。")

# --- 2. 会话状态初始化 (Session State) ---

if 'recent_searches' not in st.session_state:
    st.session_state.recent_searches = pd.DataFrame(
        columns=["代码", "公司", "价格", "市盈率(TTM)", "PEG"]
    )

# 初始化当前股票代码，确保交互时不会丢失
if 'current_ticker' not in st.session_state:
    st.session_state.current_ticker = None

# --- 3. 核心数据获取函数 ---

@st.cache_data(ttl=3600)
def get_stock_data(ticker, api_key=None):
    """
    获取单个股票的所有必要数据，包含频率限制处理。
    """
    yf_stock = yf.Ticker(ticker)
    
    # 1. YFinance 基础数据
    try:
        # 获取 info 是最容易触发限制的，添加异常处理
        yf_info = yf_stock.info
    except Exception as e:
        if "RateLimitError" in str(e):
            st.error("⚠️ 触发 Yahoo Finance 频率限制。请等待几秒钟再试。")
            return None
        return None
    
    # 检查数据有效性
    if not yf_info or 'symbol' not in yf_info:
        return None

    # 提取并重命名数据，方便后续使用
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
    
    # 计算 P/FCF (市现率)
    if data['free_cash_flow'] and data['market_cap'] and data['free_cash_flow'] > 0:
        data['p_fcf'] = data['market_cap'] / data['free_cash_flow']
    else:
        data['p_fcf'] = 0
    
    # 2. 历史价格数据 (5年)
    try:
        time.sleep(0.1)  #稍微等待，避免请求过快
        hist_price = yf_stock.history(period="5y")
        if not hist_price.empty:
            data["hist_price"] = hist_price['Close']
        else:
            data["hist_price"] = pd.Series()
    except Exception:
        data["hist_price"] = pd.Series()
    
    # 3. 计算历史 PE
    try:
        if not data["hist_price"].empty and data.get('pe_ttm') and data['pe_ttm'] > 0 and data['price'] > 0:
            quarterly_price = data["hist_price"].resample('Q').last()
            hist_pe = (quarterly_price / data['price']) * data['pe_ttm']
            hist_pe = hist_pe[(hist_pe > 5) & (hist_pe < 200)] # 过滤异常值
            data["hist_pe"] = hist_pe
        else:
            data["hist_pe"] = pd.Series()
    except Exception:
        data["hist_pe"] = pd.Series()
    
    # 4. 分析师增长率预期 (Growth Estimates)
    growth_rate = None
    
    # 方法 1: 通过 Forward EPS 和 Trailing EPS 反推
    if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
        growth_rate = ((data['eps_fwd'] - data['eps_ttm']) / data['eps_ttm']) * 100
    
    # 方法 2: FMP API (如果有 Key)
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
    
    # 方法 3: YFinance 季度增长率
    if growth_rate is None:
        try:
            growth_5y = yf_info.get('earningsQuarterlyGrowth', None)
            if growth_5y:
                growth_rate = growth_5y * 100
        except:
            pass
    
    if growth_rate is None:
        growth_rate = 10.0 # 默认值
    
    # 限制增长率在合理范围内 (-50% 到 200%)
    data["g_consensus"] = max(-50.0, min(growth_rate, 200.0))
    
    # 6. 分析师目标价
    try:
        analyst_info = yf_info.get('targetMeanPrice', None)
        data["analyst_target"] = {
            'mean': analyst_info if analyst_info else 0,
            'high': yf_info.get('targetHighPrice', 0),
            'low': yf_info.get('targetLowPrice', 0),
            'count': yf_info.get('numberOfAnalystOpinions', 0)
        }
        
        # 获取评级 (需要 FMP Key)
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
    """更新侧边栏的最近搜索列表"""
    new_entry = {
        "代码": ticker.upper(),
        "公司": data['name'][:10] + "..." if len(data['name']) > 10 else data['name'],
        "价格": f"${data['price']:.2f}",
        "市盈率(TTM)": f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') else "N/A",
        "PEG": f"{(data['pe_fwd']/data['g_consensus']):.2f}" if data.get('pe_fwd') and data['g_consensus'] else "N/A"
    }
    
    new_df = pd.DataFrame([new_entry])
    
    # 确保列名一致
    if "代码" in st.session_state.recent_searches.columns:
        st.session_state.recent_searches = st.session_state.recent_searches[
            st.session_state.recent_searches['代码'] != ticker.upper()
        ]
    
    st.session_state.recent_searches = pd.concat(
        [new_df, st.session_state.recent_searches],
        ignore_index=True
    ).head(10)

# --- 4. 侧边栏布局 ---

st.sidebar.title("🩵 股票估值分析")
st.sidebar.caption("专业投资者工具箱")

# 搜索输入框
ticker_input = st.sidebar.text_input("输入股票代码 (如 AAPL, BABA)", key="ticker_input_sidebar").strip().upper()
search_triggered = st.sidebar.button("🔍 搜索", use_container_width=True, type="primary")

# --- 核心修复：使用 Session State 处理搜索逻辑，防止刷新丢失 ---
if search_triggered and ticker_input:
    st.session_state.current_ticker = ticker_input

# 显示最近搜索
st.sidebar.divider()
st.sidebar.subheader("最近搜索记录")
if not st.session_state.recent_searches.empty:
    st.sidebar.dataframe(st.session_state.recent_searches, width=400, hide_index=True)
else:
    st.sidebar.info("暂无记录")

# --- 5. 主仪表盘 ---

# 使用 session_state 中的 ticker，而不是仅仅依赖输入框
ticker = st.session_state.current_ticker

if ticker:
    # 获取数据
    with st.spinner(f"正在获取 {ticker} 的数据..."):
        data = get_stock_data(ticker, FMP_API_KEY)

    if data is None:
        st.error(f"❌ 无法获取 {ticker} 的数据。可能是代码错误或触发了 Yahoo 频率限制。")
        st.info("💡 建议等待 30 秒后重试。")
    elif data['price'] == 0:
        st.error(f"❌ 找到了代码 {ticker}，但没有价格数据。")
    else:
        update_recent_list(ticker, data)

        # --- A. 核心指标 ---
        st.header(f"📈 {data['name']} ({ticker})")
        
        cols_metrics = st.columns(4)
        cols_metrics[0].metric("💰 当前价格", f"${data['price']:.2f}")
        cols_metrics[1].metric("📊 市盈率 (TTM)", f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') else "N/A", help="过去12个月的市盈率。衡量当前估值。")
        cols_metrics[2].metric("🔮 远期市盈率 (Fwd)", f"{data['pe_fwd']:.2f}x" if data.get('pe_fwd') else "N/A", help="基于未来一年预期收益的市盈率。")
        cols_metrics[3].metric("⚡ Beta (波动率)", f"{data['beta']:.2f}" if isinstance(data.get('beta'), (int, float)) else "N/A", help="相对于大盘的波动性。>1 表示比大盘波动大。")
        
        # 第二行：EPS 数据
        cols_eps = st.columns(4)
        cols_eps[0].metric("💵 每股收益 (TTM)", f"${data['eps_ttm']:.2f}" if data['eps_ttm'] else "N/A")
        cols_eps[1].metric("🎯 远期每股收益", f"${data['eps_fwd']:.2f}" if data['eps_fwd'] else "N/A")
        
        eps_growth = 0
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
            eps_growth = ((data['eps_fwd'] - data['eps_ttm']) / data['eps_ttm']) * 100
            cols_eps[2].metric("📈 隐含增长率", f"{eps_growth:.1f}%", 
                              help="通过 远期EPS 和 当前EPS 计算出的增长预期")
        else:
            cols_eps[2].metric("📈 隐含增长率", "N/A")
        
        cols_eps[3].metric("🏦 分析师预期增长", f"{data['g_consensus']:.1f}%")
        
        # --- 数据质量检查 ---
        st.divider()
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_ttm'] > 0:
            eps_ratio = data['eps_fwd'] / data['eps_ttm']
            if eps_ratio > 1.5:
                st.error(f"⚠️ **数据警告**: 远期 EPS 是 历史 EPS 的 {eps_ratio:.1f} 倍。这说明历史市盈率 (TTM P/E) 可能失真，请主要参考 远期市盈率 (Forward P/E)。")
            elif eps_ratio > 1.2:
                st.warning(f"💡 提示: 市场预期未来会有显著增长 (Forward EPS > TTM EPS)。")

        # 修正 Forward EPS 用于展示
        fwd_eps_display = data['eps_fwd']
        if data['eps_fwd'] and data['eps_ttm'] and data['eps_fwd'] < data['eps_ttm'] * 0.5:
             if data['g_consensus'] and data['g_consensus'] > -30:
                fwd_eps_display = data['eps_ttm'] * (1 + data['g_consensus']/100)
                st.info(f"💡 检测到异常数据，已基于增长率调整 Forward EPS 为: ${fwd_eps_display:.2f}")

        # 第三行：财务数据
        cols_value = st.columns(4)
        def fmt_mc(v):
            if v >= 1e12: return f"${v/1e12:.2f}T (万亿)"
            if v >= 1e9: return f"${v/1e9:.2f}B (十亿)"
            return f"${v/1e6:.2f}M (百万)"

        cols_value[0].metric("🏢 市值", fmt_mc(data['market_cap']) if data['market_cap'] else "N/A")
        cols_value[1].metric("📊 营收 (TTM)", fmt_mc(data['revenue_ttm']) if data['revenue_ttm'] else "N/A")
        cols_value[2].metric("💹 净利率", f"{data['profit_margin']*100:.1f}%" if data['profit_margin'] else "N/A")
        cols_value[3].metric("💸 P/FCF (市现率)", f"{data['p_fcf']:.1f}x" if data['p_fcf'] else "N/A", help="市值除以自由现金流。越低通常越好。")

        # --- 详细财务数据展开 ---
        with st.expander("📋 查看完整财务健康数据 (Financial Health)"):
            st.markdown("### 💰 盈利能力")
            p_cols = st.columns(4)
            p_cols[0].metric("ROE (净资产收益率)", f"{data['roe']*100:.1f}%" if data['roe'] else "N/A", help="巴菲特最看重的指标。>15% 为优秀。")
            p_cols[1].metric("ROA (总资产收益率)", f"{data['roa']*100:.1f}%" if data['roa'] else "N/A")
            p_cols[2].metric("毛利率", f"{data['gross_margin']*100:.1f}%" if data['gross_margin'] else "N/A")
            p_cols[3].metric("营业利润率", f"{data['operating_margin']*100:.1f}%" if data['operating_margin'] else "N/A")
            
            st.divider()
            st.markdown("### ⚖️ 资产负债表健康度")
            h_cols = st.columns(3)
            h_cols[0].metric("债务/权益比 (D/E)", f"{data['debt_to_equity']:.2f}" if data['debt_to_equity'] else "N/A", help="<1.0 通常比较安全。")
            h_cols[1].metric("流动比率", f"{data['current_ratio']:.2f}" if data['current_ratio'] else "N/A", help=">1.5 表示短期偿债能力强。")
            
            # 健康评分
            health_score = 0
            if data['debt_to_equity'] and data['debt_to_equity'] < 1.0: health_score += 1
            if data['current_ratio'] and data['current_ratio'] > 1.5: health_score += 1
            if data['free_cash_flow'] and data['free_cash_flow'] > 0: health_score += 1
            
            if health_score >= 2:
                st.success(f"✅ 财务健康评分: {health_score}/3 (良好)")
            else:
                st.warning(f"⚠️ 财务健康评分: {health_score}/3 (需关注债务风险)")

        st.divider()

        # --- B. 估值分析 (Valuation Analysis) ---
        st.header("💎 估值模型分析")
        st.markdown(f"### 当前价格: **${data['price']:.2f}**")
        
        # --- B1. P/E 模型 ---
        st.subheader("💰 1. 远期市盈率估值 (Forward P/E Model)")
        st.caption("原理：如果公司未来能达到预期的每股收益 (EPS)，且市场给予合理的市盈率 (P/E)，股价应该是多少？")
        
        # 计算历史 P/E 统计数据
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

        st.info(f"**系统推荐 (基于历史波动):** 低估: {pe_low_rec:.1f}x | 合理: {pe_mid_rec:.1f}x | 高估: {pe_high_rec:.1f}x")

        # 交互式输入 (现在因为有 Session State，调整这些不会导致页面刷新丢失)
        pe_c1, pe_c2, pe_c3 = st.columns(3)
        pe_low = pe_c1.number_input("🟢 低估 P/E", value=float(round(pe_low_rec, 1)), step=0.5, key="pe_low")
        pe_mid = pe_c2.number_input("🟡 合理 P/E", value=float(round(pe_mid_rec, 1)), step=0.5, key="pe_mid")
        pe_high = pe_c3.number_input("🔴 高估 P/E", value=float(round(pe_high_rec, 1)), step=0.5, key="pe_high")

        if fwd_eps_display and fwd_eps_display > 0:
            price_low = pe_low * fwd_eps_display
            price_mid = pe_mid * fwd_eps_display
            price_high = pe_high * fwd_eps_display
            
            res_c1, res_c2, res_c3 = st.columns(3)
            res_c1.metric("🟢 低估价格", f"${price_low:.2f}", delta=f"{(price_low/data['price'] - 1)*100:+.1f}%")
            res_c2.metric("🟡 合理价格", f"${price_mid:.2f}", delta=f"{(price_mid/data['price'] - 1)*100:+.1f}%")
            res_c3.metric("🔴 高估价格", f"${price_high:.2f}", delta=f"{(price_high/data['price'] - 1)*100:+.1f}%")
            
            # 仪表盘图表
            fig_gauge = go.Figure()
            fig_gauge.add_trace(go.Bar(
                x=['低估区', '合理区', '高估区'],
                y=[price_low, price_mid, price_high],
                marker_color=['green', 'gold', 'red'],
                text=[f'${price_low:.0f}', f'${price_mid:.0f}', f'${price_high:.0f}'],
                textposition='auto'
            ))
            fig_gauge.add_hline(y=data['price'], line_dash="dash", line_color="blue", annotation_text=f"当前价格: ${data['price']:.2f}")
            fig_gauge.update_layout(height=300, margin=dict(l=20, r=20, t=30, b=20), title="估值区间可视化")
            st.plotly_chart(fig_gauge, use_container_width=True)
        else:
            st.error("缺少远期 EPS 数据，无法进行 P/E 估值。")

        st.divider()

        # --- B2. PEG 模型 ---
        st.subheader("🚀 2. PEG 比率分析 (Growth Valuation)")
        st.caption("原理：将市盈率与增长率进行比较。彼得·林奇认为，PEG < 1 通常代表低估。")
        
        # 历史增长率计算
        g_h_default = 10.0 
        if not data['hist_price'].empty and len(data['hist_price']) > 200:
             start_p = data['hist_price'].iloc[0]
             end_p = data['hist_price'].iloc[-1]
             if start_p > 0:
                 g_h_default = ((end_p/start_p)**(1/5) - 1) * 100
        
        st.markdown("#### 增长率假设调整")
        g_col1, g_col2 = st.columns(2)
        
        with g_col1:
            st.caption("混合增长率计算器")
            g_hist_in = st.number_input("历史增长率 %", value=float(round(g_h_default, 1)), step=0.5, key="g_hist_in")
            weight_in = st.slider("分析师预期权重", 0.0, 1.0, 0.7, 0.1, key="w_in", help="越靠近1.0，越依赖分析师对未来的预测。")
            
            g_blended = (data['g_consensus'] * weight_in) + (g_hist_in * (1 - weight_in))
            st.metric("混合增长率", f"{g_blended:.1f}%")
        
        with g_col2:
            st.caption("PEG 评估结果")
            if g_blended > 0 and data['pe_fwd'] > 0:
                peg = data['pe_fwd'] / g_blended
                st.metric("远期 PEG", f"{peg:.2f}x")
                
                if peg < 0.8: st.success("✅ 被低估 (PEG < 0.8)")
                elif peg < 1.2: st.info("🟡 估值合理 (PEG 0.8 - 1.2)")
                elif peg < 2.0: st.warning("🔴 被高估 (PEG 1.2 - 2.0)")
                else: st.error("❌ 极度高估 (PEG > 2.0)")
            else:
                st.write("数据不足，无法计算 PEG（增长率可能为负）。")

        st.divider()

        # --- B3. 分析师目标价 ---
        st.subheader("🏦 3. 华尔街分析师共识")
        an_tgt = data.get('analyst_target', {})
        if an_tgt.get('mean', 0) > 0:
            ac1, ac2, ac3 = st.columns(3)
            ac1.metric("最低目标价", f"${an_tgt['low']:.2f}")
            ac2.metric("平均目标价", f"${an_tgt['mean']:.2f}", delta=f"{(an_tgt['mean']/data['price'] - 1)*100:+.1f}%")
            ac3.metric("最高目标价", f"${an_tgt['high']:.2f}")
            st.caption(f"基于 {an_tgt['count']} 位分析师的预测")
        else:
            st.info("暂无分析师目标价数据。")

        # --- C. 历史图表 ---
        st.divider()
        st.header("📊 5年历史数据回顾")
        
        if not data['hist_price'].empty:
            chart_c1, chart_c2 = st.columns(2)
            with chart_c1:
                st.subheader("股价走势")
                st.line_chart(data['hist_price'], height=300)
            with chart_c2:
                if not data['hist_pe'].empty:
                    st.subheader("市盈率 (P/E) 走势")
                    st.line_chart(data['hist_pe'], height=300)
        else:
            st.write("无历史数据。")
            
        # --- D. 帮助与局限性 ---
        st.divider()
        with st.expander("📚 指标详解与局限性 (必读)"):
            st.markdown("""
            ### 1. 指标意义
            - **市盈率 (P/E)**: 回本年限的粗略估计。20倍PE意味着按当前盈利，20年回本。
            - **Forward P/E**: 基于未来预测的市盈率。比历史PE更适合看成长股。
            - **PEG**: 弥补了PE不看增长的缺点。PEG=1 代表估值与增长率匹配。PEG<1 通常代表便宜。
            - **Beta**: 风险指标。Beta=1.5 代表大盘跌1%，它可能跌1.5%。
            - **ROE**: 巴菲特最爱。公司用股东的钱赚钱的能力。长期回报率通常趋向于ROE。

            ### 2. 工具局限性 (Limitations)
            - **数据延迟**: 免费版 Yahoo Finance 数据可能有15分钟延迟，不适合日内超短线交易。
            - **分析师预测偏差**: "分析师预期增长"和"目标价"经常出错，只能作为参考，不能作为真理。
            - **简单模型**: PEG 和 P/E 估值法是线性模型，无法涵盖宏观经济变化、利率调整或公司突发暴雷的风险。
            - **API 限制**: 如果你搜索太快，Yahoo 会暂时封锁你的 IP (Rate Limit)。如果出现红色错误，请休息一分钟再试。
            """)

else:
    st.info("👈 请在左侧侧边栏输入股票代码 (如 NVDA) 并点击搜索。")
    st.markdown("""
    ### 快速入门指南
    1. **输入代码**: 在侧边栏输入美股代码。
    2. **看概览**: 检查 P/E、EPS 和 财务健康评分。
    3. **玩估值**: 
       - 在 **P/E 模型** 中，调整合理的 P/E 倍数，看目标价是多少。
       - 在 **PEG 模型** 中，调整预期增长率，看股票是否便宜。
    """)
