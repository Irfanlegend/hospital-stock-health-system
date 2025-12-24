🚀 Live Demo

👉 https://hospital-stock-health-system.streamlit.app/  

🏥 Hospital Stock Health System
AI-Powered Medicine Inventory Monitoring & Stockout Prevention

📘 Overview

The Hospital Stock Health System is an AI-driven inventory monitoring dashboard that helps hospitals prevent critical medicine stockouts using:

Snowflake Dynamic Tables
Python + Streamlit
Automated analytics
Real-time stock visualization

This ensures hospitals always maintain safe levels of essential medicines and avoid patient-risk scenarios.

🚀 Key Features

🔎 Real-Time Monitoring

Color-coded heatmap (Critical, Warning, Healthy)
Instant visibility of risk levels.

📊 AI-Driven Analytics

Automatic average daily usage calculation

Stockout prediction
Priority scoring engine

📦 Smart Reorder Recommendations

Auto-calculated reorder quantity
Priority-based recommendations
One-click export

📈 Trends & Comparison

Medicine-wise usage trends
Hospital performance leaderboard
Multi-hospital comparison


🌩 Powered by Snowflake

Dynamic Tables auto-refresh backend logic
Fast data computation with Snowflake Warehouse
Clean, scalable, secure architecture.

🛠 Snowflake Tools Used

STOCK_RECORDS → Main data table
Dynamic Tables → Auto-refresh logic for stock health
REORDER_RECOMMENDATIONS → AI reorder engine
Tasks → Scheduled updates
SQL Worksheets → Debugging & verification
COMPUTE_WH → Snowflake compute layer.

How to Run Locally

1. Clone the repository  git clone https://github.com/Irfanlegend/hospital-stock-health-system.git
cd hospital-stock-health-system

2. Install dependencies   pip install -r requirements.txt

3. Add Streamlit Secrets  Create file:   .streamlit/secrets.toml
Paste this inside: SNOWFLAKE_USER="your_user"
SNOWFLAKE_PASSWORD="your_pass"
SNOWFLAKE_ACCOUNT="your_account"
SNOWFLAKE_WAREHOUSE="COMPUTE_WH"
SNOWFLAKE_DATABASE="HOSPITAL_STOCK_DB"
SNOWFLAKE_SCHEMA="INVENTORY"


4. Run the application            streamlit run app.py




🌐 Live Demo URL

👉 https://hospital-stock-health-system.streamlit.app/

🎥 Demo Video

👉 https://youtu.be/your-demo-video


📁 Project Structure

hospital-stock-health-system/
│── app.py
│── sample_data.csv
│── requirements.txt
│── README.md
│── .streamlit/
│     └── secrets.toml



