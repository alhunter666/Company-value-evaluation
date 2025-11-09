import streamlit as st
import yfinance as yf
import requests
import pandas as pd

# --- 1. 配置与密钥 ---

# 设置页面为宽屏模式，这是 Streamlit 的推荐做法
st.set_page_config(layout="wide")

# 从 Streamlit Secrets 安全地读取 FMP API 密钥
FMP_API_KEY = st.secrets.get("FMP_API_KEY")

# 关键：检查密钥是否存在。如果部署时未设置 Secrets，App将停止并显示错误
if not FMP_API_KEY:
    st.error("FMP_API_KEY 未在 Streamlit Secrets 中设置！请添加它以便 App 运行。")
    st.stop()

# --- 2. 会话状态 (Session State) 初始化 ---

# 用于存储最近10次搜索的记录
# 我们将其初始化为一个空的 DataFrame，并定义好列名
if 'recent_searches' not in st.session_state:
    st.session_state.recent_searches = pd.DataFrame(
        columns=["代码", "公司", "价格", "Trailing PE", "PEG 中枢"]
    )

# --- 3. 核心数据获取函数 ---

@st.cache_data(ttl=3600)  # 将数据缓存1小时 (3600秒)
def get_stock_data(ticker):
    """
    获取单个股票所需的所有数据 (YFinance + FMP)。
    """
    yf_stock = yf.Ticker(ticker)
    
    # 1. YFinance 数据
    # 使用 .get() 方法安全地获取数据，如果键不存在则返回 0 或 "N/A"
    yf_info = yf_stock.info
    data = {
        "name": yf_info.get('longName', ticker),
        "price": yf_info.get('currentPrice', 0),
        "beta": yf_info.get('beta', 'N/A'),
        "eps_ttm": yf_info.get('trailingEps', 0),
        "eps_fwd": yf_info.get('forwardEps', 0),
        "pe_ttm": yf_info.get('trailingPE', 0),
        "pe_fwd": yf_info.get('forwardPE', 0)
    }
    
    # 2. FMP 数据 (分析师增长率 G)
    url_g = f"https://financialmodelingprep.com/api/v3/analyst-estimates/{ticker}?apikey={FMP_API_KEY}"
    try:
        g_data = requests.get(url_g).json()
        # 安全地获取第一个条目中的 'growth' 值
        data["g_consensus"] = float(g_data[0].get('growth', 0)) if g_data else 0
    except requests.RequestException as e:
        st.warning(f"无法获取 FMP 增长率数据: {e}")
        data["g_consensus"] = 0

    # 3. FMP 数据 (历史PE, EPS - 5年 = 20个季度)
    url_hist = f"https://financialmodelingprep.com/api/v3/historical-ratios/{ticker}?limit=20&apikey={FMP_API_KEY}"
    try:
        hist_data = requests.get(url_hist).json()
        hist_df = pd.DataFrame(hist_data).iloc[::-1]  # 倒序，使日期从早到晚
        if not hist_df.empty:
            hist_df['date'] = pd.to_datetime(hist_df['date'])
            hist_df = hist_df.set_index('date')
            hist_df['peRatio'] = pd.to_numeric(hist_df['peRatio'])
            hist_df['eps'] = pd.to_numeric(hist_df['eps'])
        data["hist_ratios"] = hist_df
    except requests.RequestException as e:
        st.warning(f"无法获取 FMP 历史比率数据: {e}")
        data["hist_ratios"] = pd.DataFrame()

    # 4. YFinance 数据 (历史价格 - 5年)
    data["hist_price"] = yf_stock.history(period="5y")['Close']
    
    return data

def update_recent_list(ticker, data, price_mid_peg):
    """
    更新侧边栏的最近10条搜索记录。
    """
    new_entry = {
        "代码": ticker.upper(),
        "公司": data['name'],
        "价格": f"${data['price']:.2f}",
        "Trailing PE": f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') else "N/A",
        "PEG 中枢": f"${price_mid_peg:.2f}"
    }
    
    # 转换为 DataFrame 以便合并
    new_df_entry = pd.DataFrame([new_entry])
    
    # 从旧记录中删除这个 ticker (如果存在)
    st.session_state.recent_searches = st.session_state.recent_searches[
        st.session_state.recent_searches['代码'] != ticker.upper()
    ]
    
    # 将新记录添加到 DataFrame 顶部，并保持最多10条
    st.session_state.recent_searches = pd.concat(
        [new_df_entry, st.session_state.recent_searches],
        ignore_index=True
    ).head(10)

# --- 4. 侧边栏 (Sidebar) 布局 ---

st.sidebar.title("估值分析器")
st.sidebar.caption("使用历史PE法与PEG法进行估值")

ticker = st.sidebar.text_input("输入股票代码 (e.g., AAPL)", key="ticker_input")
search_button = st.sidebar.button("搜索", use_container_width=True)

st.sidebar.subheader("最近10次搜索")
st.sidebar.dataframe(
    st.session_state.recent_searches,
    use_container_width=True,
    hide_index=True
)

# --- 5. 主面板 (Main Panel) 布局 ---

if search_button and ticker:
    try:
        data = get_stock_data(ticker)
        
        # --- A. 核心指标 ---
        st.header(f"{data['name']} ({ticker.upper()})")
        
        # 检查数据是否有效
        if data['price'] == 0:
            st.error(f"无法获取 {ticker} 的有效数据。请检查股票代码。")
            st.stop()
        
        cols_metrics = st.columns(4)
        cols_metrics[0].metric("当前价格", f"${data['price']:.2f}")
        cols_metrics[1].metric("Trailing PE (TTM)", f"{data['pe_ttm']:.2f}x" if data.get('pe_ttm') else "N/A")
        cols_metrics[2].metric("Forward PE (远期)", f"{data['pe_fwd']:.2f}x" if data.get('pe_fwd') else "N/A")
        cols_metrics[3].metric("Beta (5年风险)", f"{data['beta']:.2f}" if isinstance(data.get('beta'), (int, float)) else "N/A")
        
        cols_eps = st.columns(4)
        cols_eps[1].metric("Trailing EPS (TTM)", f"${data['eps_ttm']:.2f}")
        cols_eps[2].metric("Forward EPS (远期)", f"${data['eps_fwd']:.2f}")
        st.divider()

        # --- B. 估值计算 ---
        st.header("合格价格区间 (Valuation Range)")
        
        col1, col2 = st.columns(2)
        
        price_mid_peg = 0.0  # 初始化PEG中枢价
        
        # -- B1. 历史PE法 --
        with col1:
            with st.container(border=True):
                st.subheader("模型一：历史PE法")
                st.caption("基于 Trailing PE 的历史情绪回归")
                
                hist_pe = data['hist_ratios']['peRatio'].dropna()
                if not hist_pe.empty:
                    p_mean = hist_pe.mean()
                    p_std = hist_pe.std()
                    
                    st.write(f"历史平均PE (P): **{p_mean:.2f}x**")
                    st.write(f"历史标准差 (SD): **{p_std:.2f}x**")
                    st.divider()
                    
                    price_low_hist = (p_mean - p_std) * data['eps_ttm']
                    price_mid_hist = p_mean * data['eps_ttm']
                    price_high_hist = (p_mean + p_std) * data['eps_ttm']
                    
                    st.metric("估值中枢 (P * TTM EPS)", f"${price_mid_hist:.2f}")
                    st.write(f"估值区间: **${price_low_hist:.2f} - ${price_high_hist:.2f}**")
                    
                    # 可靠性检查
                    if data['price'] >= price_low_hist and data['price'] <= price_high_hist:
                        st.success("可靠性: 当前价格在历史PE区间内。")
                    elif data['price'] > price_high_hist:
                        st.warning("可靠性: 当前价格高于历史PE区间。")
                    else:
                        st.info("可靠性: 当前价格低于历史PE区间。")
                else:
                    st.error("无法获取历史PE数据。")

        # -- B2. PEG法 --
        with col2:
            with st.container(border=True):
                st.subheader("模型二：PEG估值法")
                st.caption("基于未来增长潜力")
                
                g_c = data['g_consensus']
                # G_History (我们用历史EPS的CAGR来计算)
                hist_eps = data['hist_ratios']['eps'].dropna()
                g_h_default = 10.0 # 默认值
                if len(hist_eps) >= 2:
                    # 计算5年CAGR
                    start_eps = hist_eps.iloc[0]
                    end_eps = hist_eps.iloc[-1]
                    years = len(hist_eps) / 4.0 # 季度数据转为年
                    if start_eps > 0 and end_eps > 0:
                         g_h_default = ((end_eps / start_eps) ** (1/years) - 1) * 100.0

                g_h = st.number_input("历史EPS增长率 % (自动计算)", value=g_h_default, step=0.5, key="g_history_input")
                
                weight = st.slider("分析师G权重 (W_c)", 0.0, 1.0, 0.7, 0.05, key="g_weight_slider", help="1.0代表100%相信分析师预测, 0.0代表100%相信历史增长率")
                g_blended = (g_c * weight) + (g_h * (1 - weight))
                
                st.write(f"分析师 G: **{g_c:.2f}%** | 历史 G: **{g_h:.2f}%**")
                st.write(f"混合增长率 G_Blended: **{g_blended:.2f}%**")
                st.divider()

                if g_blended > 0 and data['pe_ttm'] is not None:
                    current_peg = (data['pe_ttm']) / g_blended
                    st.metric("当前PEG (基于混合G)", f"{current_peg:.2f}")
                    
                    price_low_peg = 0.8 * g_blended * data['eps_ttm'] # PEG*G*EPS
                    price_mid_peg = 1.0 * g_blended * data['eps_ttm']
                    price_high_peg = 1.5 * g_blended * data['eps_ttm']
                    
                    st.metric("估值中枢 (PEG=1.0)", f"${price_mid_peg:.2f}")
                    st.write(f"估值区间: **${price_low_peg:.2f} - ${price_high_peg:.2f}**")
                    
                    if current_peg < 1.0:
                        st.success(f"可靠性: 当前PEG ({current_peg:.2f}) < 1.0")
                    else:
                        st.warning(f"可靠性: 当前PEG ({current_peg:.2f}) > 1.0")
                else:
                    st.metric("当前PEG (基于混合G)", "N/A")
                    st.error("可靠性: 增长率为负或零，PEG法失效。")
        
        # 更新历史记录
        update_recent_list(ticker, data, price_mid_peg)

        # --- C. 历史图表 ---
        st.divider()
        st.header("历史发展过程 (5年)")
        
        st.subheader("股价走势 (5年)")
        st.line_chart(data['hist_price'], use_container_width=True)
        
        st.subheader("历史 Trailing PE 比率 (季度)")
        st.line_chart(data['hist_ratios']['peRatio'], use_container_width=True)
        
        st.subheader("历史 EPS (TTM, 季度)")
        st.bar_chart(data['hist_ratios']['eps'], use_container_width=True)

    except Exception as e:
        st.error(f"无法获取股票 {ticker} 的数据。")
        st.error(f"详细错误: {e}")
        st.exception(e)  # 打印完整的错误堆栈以供调试
        
else:
    st.info("请在侧边栏输入股票代码并点击“搜索”以开始分析。")
