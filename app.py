import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas

# -------------------- PAGE CONFIG --------------------
st.set_page_config(
    page_title="Hospital Stock Management System",
    page_icon="🏥",
    layout="wide"
)

st.title("🏥 Hospital Medicine Stock Management System")
st.markdown("### AI-Powered Inventory Monitoring & Stockout Prevention")
st.markdown("---")


# -------------------- SNOWFLAKE CONNECTION --------------------
def get_connection():
    try:
        conn = snowflake.connector.connect(
            user=st.secrets["SNOWFLAKE_USER"],
            password=st.secrets["SNOWFLAKE_PASSWORD"],
            account=st.secrets["SNOWFLAKE_ACCOUNT"],
            warehouse=st.secrets["SNOWFLAKE_WAREHOUSE"],
            database=st.secrets["SNOWFLAKE_DATABASE"],
            schema=st.secrets["SNOWFLAKE_SCHEMA"]
        )
        return conn
    except Exception as e:
        st.error(f"❌ Snowflake Connection Failed: {e}")
        return None


# -------------------- LOAD CSV TO SNOWFLAKE --------------------
def load_csv_data(conn):
    try:
        st.info("Setting up Snowflake Tables...")

        cursor = conn.cursor()

        # Create main STOCK_RECORDS table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS STOCK_RECORDS (
                date DATE,
                hospital_id VARCHAR(10),
                hospital_name VARCHAR(150),
                medicine_name VARCHAR(150),
                opening_stock NUMBER,
                received NUMBER,
                issued NUMBER,
                closing_stock NUMBER,
                lead_time_days NUMBER,
                min_stock_level NUMBER
            )
        """)

        # Drop existing Dynamic Tables
        cursor.execute("DROP DYNAMIC TABLE IF EXISTS CURRENT_STOCK_STATUS")
        cursor.execute("DROP DYNAMIC TABLE IF EXISTS REORDER_RECOMMENDATIONS")

        # CREATE dynamic table CURRENT_STOCK_STATUS
        cursor.execute("""
            CREATE OR REPLACE DYNAMIC TABLE CURRENT_STOCK_STATUS
            TARGET_LAG = '1 minute'
            WAREHOUSE = COMPUTE_WH AS
            SELECT 
                hospital_id,
                hospital_name,
                medicine_name,
                closing_stock AS current_stock,
                min_stock_level,
                lead_time_days,
                AVG(issued) OVER (
                    PARTITION BY hospital_id, medicine_name
                    ORDER BY date
                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                ) AS avg_daily_usage,
                CASE
                    WHEN closing_stock <= min_stock_level THEN 'CRITICAL'
                    WHEN closing_stock /
                        NULLIF(AVG(issued) OVER (
                            PARTITION BY hospital_id, medicine_name
                            ORDER BY date
                            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                        ),0)
                        <= lead_time_days THEN 'WARNING'
                    ELSE 'HEALTHY'
                END AS stock_status,
                ROUND(
                    closing_stock /
                    NULLIF(AVG(issued) OVER (
                        PARTITION BY hospital_id, medicine_name
                        ORDER BY date
                        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                    ),0), 1
                ) AS days_until_stockout,
                date
            FROM STOCK_RECORDS
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY hospital_id, medicine_name ORDER BY date DESC
            ) = 1;
        """)

        # CREATE DYNAMIC TABLE for reorder recommendations
        cursor.execute("""
            CREATE OR REPLACE DYNAMIC TABLE REORDER_RECOMMENDATIONS
            TARGET_LAG = '1 minute'
            WAREHOUSE = COMPUTE_WH AS
            SELECT 
                hospital_id,
                hospital_name,
                medicine_name,
                current_stock,
                avg_daily_usage,
                lead_time_days,
                stock_status,
                days_until_stockout,
                CASE
                    WHEN stock_status='CRITICAL' THEN GREATEST(ROUND((avg_daily_usage*(lead_time_days+30)) - current_stock),0)
                    WHEN stock_status='WARNING' THEN GREATEST(ROUND((avg_daily_usage*(lead_time_days+15)) - current_stock),0)
                    ELSE 0
                END AS recommended_order_quantity,
                CASE
                    WHEN stock_status='CRITICAL' THEN 1
                    WHEN stock_status='WARNING' THEN 2
                    ELSE 3
                END AS priority
            FROM CURRENT_STOCK_STATUS
        """)

        st.success("✅ Snowflake Dynamic Tables Ready!")

        # Read CSV
        df = pd.read_csv("sample_data.csv")

        # Truncate table before insert
        cursor.execute("TRUNCATE TABLE STOCK_RECORDS")
        write_pandas(conn, df, "STOCK_RECORDS")

        st.success("✅ Sample Data Loaded Successfully!")

    except Exception as e:
        st.error(f"❌ Error Loading Data: {e}")


# -------------------- FETCH DATA --------------------
def fetch_data(conn):
    try:
        stock_df = pd.read_sql("SELECT * FROM CURRENT_STOCK_STATUS", conn)
        reorder_df = pd.read_sql("SELECT * FROM REORDER_RECOMMENDATIONS ORDER BY priority", conn)
        return stock_df, reorder_df
    except Exception as e:
        st.error(f"❌ Error Fetching Data: {e}")
        return None, None


# -------------------- KPI BOX HELPER --------------------
def metric_box(title, value, color):
    st.markdown(f"""
        <div style='padding:15px;border-left:6px solid {color};
        background:white;border-radius:6px;box-shadow:0 2px 4px rgba(0,0,0,0.1);'>
        <h4 style='margin:0;color:#444;'>{title}</h4>
        <h2 style='margin:0;color:{color};'>{value}</h2>
        </div>
    """, unsafe_allow_html=True)


# -------------------- HEATMAP --------------------
def create_heatmap(df):
    pivot = df.pivot_table(index="medicine_name", columns="hospital_name",
                           values="current_stock", fill_value=0)
    fig = px.imshow(
        pivot,
        labels=dict(x="Hospital", y="Medicine", color="Stock"),
        color_continuous_scale=["#dc3545", "#ffc107", "#28a745"]
    )
    fig.update_layout(height=450)
    return fig


# -------------------- MAIN APP --------------------
conn = get_connection()
if not conn:
    st.stop()

# TOP BUTTON
if st.button("📥 Load Sample CSV to Snowflake"):
    load_csv_data(conn)

stock_df, reorder_df = fetch_data(conn)
if stock_df is None:
    st.stop()


# -------------------- FILTERS --------------------
st.subheader("🔎 Filter Data")

c1, c2, c3 = st.columns(3)

medicine_filter = c1.selectbox("Select Medicine", ["All"] + sorted(stock_df["medicine_name"].unique()))
hospital_filter = c2.selectbox("Select Hospital", ["All"] + sorted(stock_df["hospital_name"].unique()))
reset_btn = c3.button("Reset Filters")

filtered_df = stock_df.copy()

if medicine_filter != "All":
    filtered_df = filtered_df[filtered_df["medicine_name"] == medicine_filter]

if hospital_filter != "All":
    filtered_df = filtered_df[filtered_df["hospital_name"] == hospital_filter]


# -------------------- KPI SECTION --------------------
st.subheader("📊 Stock Overview")

col1, col2, col3, col4 = st.columns(4)

metric_box("Total Items", len(filtered_df), "#007bff")
metric_box("Critical", len(filtered_df[filtered_df['stock_status']=="CRITICAL"]), "#dc3545")
metric_box("Warning", len(filtered_df[filtered_df['stock_status']=="WARNING"]), "#ffc107")
metric_box("Healthy", len(filtered_df[filtered_df['stock_status']=="HEALTHY"]), "#28a745")

st.markdown("---")


# -------------------- HEATMAP --------------------
st.subheader("🔥 Stock Heatmap")
if len(filtered_df) > 0:
    st.plotly_chart(create_heatmap(filtered_df), use_container_width=True)
else:
    st.info("No data available for selected filters.")


# -------------------- REORDER TABLE --------------------
st.markdown("---")
st.subheader("📦 Reorder Recommendations")

if reorder_df is not None and len(reorder_df) > 0:
    st.dataframe(reorder_df, use_container_width=True)
else:
    st.info("All stocks are healthy — no reorder required!")


# -------------------- FULL STOCK TABLE --------------------
st.markdown("---")
st.subheader("📋 Complete Stock Inventory")

st.dataframe(filtered_df, use_container_width=True)
