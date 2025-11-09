import streamlit as st
import yfinance as yf
import requests
import pandas as pd
import numpy as np

# --- 1. 配置与密钥 ---

st.set_page_config(layout="wide", page_title="股票估值分析器", page_icon="📊")

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
        "pe_fwd": yf_info.get('forwardPE', 0)
    }
    
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
            
            # --- A. 核心指标 ---
            st.header(f"📈 {data['name']} ({ticker})")
            
            if data['price'] == 0:
                st.error(f"❌ 无法获取 {ticker} 的有效数据。请检查股票代码是否正确。")
                st.stop()
            
            cols_metrics = st.columns(4)
            cols_metrics[0].metric("💰 当前价格", f"${data['price']:.2f}")
            cols_metrics[1].metric("📊 Trailing PE (TTM)", f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') and data['pe_ttm'] > 0 else "N/A")
            cols_metrics[2].metric("🔮 Forward PE (远期)", f"{data['pe_fwd']:.2f}x" if data.get('pe_fwd') and data['pe_fwd'] > 0 else "N/A")
            cols_metrics[3].metric("⚡ Beta (5年风险)", f"{data['beta']:.2f}" if isinstance(data.get('beta'), (int, float)) else "N/A")
            
            cols_eps = st.columns(4)
            cols_eps[1].metric("💵 Trailing EPS (TTM)", f"${data['eps_ttm']:.2f}" if data['eps_ttm'] else "N/A")
            cols_eps[2].metric("🎯 Forward EPS (远期)", f"${data['eps_fwd']:.2f}" if data['eps_fwd'] else "N/A")
            st.divider()

            # --- B. 估值对比：当前价格 vs 合理区间 ---
            st.header("💎 估值分析：当前价格 vs 合理区间")
            
            # 显示当前价格（大号突出）
            st.markdown(f"### 📍 当前股价: **${data['price']:.2f}**")
            st.divider()
            
            # 存储估值结果
            valuation_results = {}
            price_mid_peg = 0.0
            
            # -- B1. 历史PE法 --
            st.subheader("📊 方法一：历史PE估值法")
            hist_pe = data['hist_pe'].dropna() if not data['hist_pe'].empty else pd.Series()
            
            if not hist_pe.empty and len(hist_pe) >= 4 and data['eps_ttm'] and data['eps_ttm'] > 0:
                p_mean = hist_pe.mean()
                p_std = hist_pe.std()
                
                price_low_hist = (p_mean - p_std) * data['eps_ttm']
                price_mid_hist = p_mean * data['eps_ttm']
                price_high_hist = (p_mean + p_std) * data['eps_ttm']
                
                valuation_results['hist_pe'] = {
                    'low': price_low_hist,
                    'mid': price_mid_hist,
                    'high': price_high_hist,
                    'method': '历史PE法'
                }
                
                col1, col2, col3 = st.columns(3)
                col1.metric("🔻 低估区间", f"${price_low_hist:.2f}", help="历史平均PE - 1标准差")
                col2.metric("🎯 合理中枢", f"${price_mid_hist:.2f}", help="历史平均PE")
                col3.metric("🔺 高估区间", f"${price_high_hist:.2f}", help="历史平均PE + 1标准差")
                
                # 评估建议
                if data['price'] < price_low_hist:
                    discount_pct = ((price_low_hist - data['price']) / price_low_hist * 100)
                    st.success(f"✅ **买入机会**: 当前价格 ${data['price']:.2f} 低于低估区间 {discount_pct:.1f}%，可能被严重低估！")
                elif data['price'] <= price_mid_hist:
                    st.success(f"✅ **合理偏低**: 当前价格在低估区间内，估值合理偏低。")
                elif data['price'] <= price_high_hist:
                    st.info(f"💡 **合理偏高**: 当前价格在合理区间内，估值略高但可接受。")
                else:
                    over_pct = ((data['price'] - price_high_hist) / price_high_hist * 100)
                    st.warning(f"⚠️ **高估风险**: 当前价格高于高估区间 {over_pct:.1f}%，可能被高估。")
                
                with st.expander("📈 查看计算详情"):
                    st.write(f"- 历史平均PE: {p_mean:.2f}x")
                    st.write(f"- 历史标准差: {p_std:.2f}x")
                    st.write(f"- TTM EPS: ${data['eps_ttm']:.2f}")
            else:
                st.warning("⚠️ 历史PE数据不足，无法使用此方法估值。")
            
            st.divider()
            
            # -- B2. PEG法 --
            st.subheader("🚀 方法二：PEG增长估值法")
            
            g_c = data['g_consensus']
            
            # 计算历史增长率（用历史价格CAGR）
            g_h_default = 10.0
            
            if not data['hist_price'].empty:
                try:
                    prices_sorted = data['hist_price'].sort_index()
                    
                    # 确保有足够的历史数据（至少1年）
                    if len(prices_sorted) >= 252:  # 252个交易日约等于1年
                        start_price = prices_sorted.iloc[0]
                        end_price = prices_sorted.iloc[-1]
                        
                        # 计算实际年数
                        start_date = prices_sorted.index[0]
                        end_date = prices_sorted.index[-1]
                        years = (end_date - start_date).days / 365.25
                        
                        if start_price > 0 and end_price > 0 and years > 0:
                            # 计算年化复合增长率
                            price_cagr = ((end_price / start_price) ** (1 / years) - 1) * 100.0
                            # 限制在合理范围
                            g_h_default = max(-50.0, min(price_cagr, 200.0))
                except Exception as e:
                    g_h_default = 10.0
            
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                g_h = st.number_input("📊 历史增长率 %", value=g_h_default, step=0.5, key="g_history_input", help="基于历史EPS的年复合增长率")
            with col_g2:
                weight = st.slider("⚖️ 分析师权重", 0.0, 1.0, 0.7, 0.05, key="g_weight_slider", help="分析师预测的可信度权重")
            
            g_blended = (g_c * weight) + (g_h * (1 - weight))
            st.info(f"🔄 混合增长率: 分析师 {g_c:.1f}% × {weight:.0%} + 历史 {g_h:.1f}% × {1-weight:.0%} = **{g_blended:.2f}%**")
            
            if g_blended > 0 and data['pe_ttm'] and data['pe_ttm'] > 0 and data['eps_ttm'] and data['eps_ttm'] > 0:
                # PEG估值区间
                price_low_peg = 0.8 * g_blended * data['eps_ttm']
                price_mid_peg = 1.0 * g_blended * data['eps_ttm']
                price_high_peg = 1.5 * g_blended * data['eps_ttm']
                
                valuation_results['peg'] = {
                    'low': price_low_peg,
                    'mid': price_mid_peg,
                    'high': price_high_peg,
                    'method': 'PEG法'
                }
                
                col1, col2, col3 = st.columns(3)
                col1.metric("🔻 保守估值", f"${price_low_peg:.2f}", help="PEG = 0.8")
                col2.metric("🎯 合理估值", f"${price_mid_peg:.2f}", help="PEG = 1.0")
                col3.metric("🔺 乐观估值", f"${price_high_peg:.2f}", help="PEG = 1.5")
                
                # 当前PEG
                current_peg = data['pe_ttm'] / g_blended
                st.metric("📊 当前PEG比率", f"{current_peg:.2f}", help="当前PE / 增长率")
                
                # 评估建议
                if data['price'] < price_low_peg:
                    discount_pct = ((price_low_peg - data['price']) / price_low_peg * 100)
                    st.success(f"✅ **强烈买入**: 当前价格 ${data['price']:.2f} 低于保守估值 {discount_pct:.1f}%，增长潜力巨大！")
                elif data['price'] <= price_mid_peg:
                    st.success(f"✅ **合理买入**: 当前价格低于合理估值，PEG < 1.0，估值吸引。")
                elif data['price'] <= price_high_peg:
                    st.info(f"💡 **持有观望**: 当前价格在合理区间内，PEG适中。")
                else:
                    over_pct = ((data['price'] - price_high_peg) / price_high_peg * 100)
                    st.warning(f"⚠️ **考虑减仓**: 当前价格高于乐观估值 {over_pct:.1f}%，增长预期已被充分计价。")
                
                with st.expander("🔍 查看计算详情"):
                    st.write(f"- 当前PE: {data['pe_ttm']:.2f}x")
                    st.write(f"- 混合增长率: {g_blended:.2f}%")
                    st.write(f"- 当前PEG: {current_peg:.2f}")
                    st.write(f"- TTM EPS: ${data['eps_ttm']:.2f}")
            else:
                st.error("⚠️ 增长率为负或数据不足，PEG法不适用。")
            
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
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("📍 当前价格", f"${data['price']:.2f}")
                col2.metric("🔻 综合低估区", f"${avg_low:.2f}")
                col3.metric("🎯 综合合理价", f"${avg_mid:.2f}")
                col4.metric("🔺 综合高估区", f"${avg_high:.2f}")
                
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
            
            update_recent_list(ticker, data, price_mid_peg)

            # --- C. 历史图表 ---
            st.divider()
            st.header("📊 历史发展过程 (5年)")
            
            chart_cols = st.columns(2)
            
            with chart_cols[0]:
                st.subheader("💹 股价走势")
                if not data['hist_price'].empty:
                    st.line_chart(data['hist_price'], height=300)
                else:
                    st.info("暂无股价历史数据")
            
            with chart_cols[1]:
                st.subheader("📈 历史 PE 比率")
                if not data['hist_pe'].empty:
                    st.line_chart(data['hist_pe'], height=300)
                else:
                    st.info("暂无PE历史数据")

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
