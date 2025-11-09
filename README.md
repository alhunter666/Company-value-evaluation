# Company-value-evaluation
# Stock Valuation Dashboard

A Streamlit web application for performing fundamental stock valuation using historical PE ratios and the PEG ratio. This dashboard provides a one-stop view to determine a "fair value" range for a stock.

 
*(您可以在此处添加一张您的App运行时的截图)*

---

## ✨ Features

* **Dual-Model Valuation:** Calculates a "fair price" range using:
    * **Model 1: Historical PE Ratio:** Based on the historical mean and standard deviation of the Trailing PE (TTM). This shows what the market has *historically* been willing to pay.
    * **Model 2: PEG Ratio:** Based on a blended growth rate ("G"). This values the company based on its *future* growth potential.
* **Key Metrics:** Displays essential real-time data:
    * Current Price
    * Trailing PE (TTM)
    * Forward PE
    * Trailing EPS (TTM)
    * Forward EPS
    * Beta (5-Year)
* **Interactive Controls:**
    * **Blended "G":** An interactive slider lets you adjust your confidence between analyst consensus growth (G_consensus) and historical growth (G_history).
    * **"G" Input:** Manually input the historical growth rate for your sanity check.
* **Historical Context:**
    * 5-Year Price Chart
    * Quarterly Historical Trailing PE Chart
    * Quarterly Historical Trailing EPS (TTM) Chart
* **Search History:**
    * The sidebar automatically saves and displays your last 10 searched tickers for quick reference.

## 🛠️ Technology Stack

* **Framework:** [Streamlit](https://streamlit.io/)
* **Data Sources:**
    * [**yfinance**](https://pypi.org/project/yfinance/): For real-time price data, key ratios (PE, EPS), and company info.
    * [**Financial Modeling Prep (FMP)**](https://financialmodelingprep.com/): For analyst growth estimates (G) and deep historical quarterly PE/EPS ratios.

---

## 🚀 Getting Started (Local Setup)

Follow these steps to run the app on your local machine.

### 1. Clone the Repository
```bash
git clone [https://github.com/YourUsername/stock-dashboard.git](https://github.com/YourUsername/stock-dashboard.git)
cd stock-dashboard
