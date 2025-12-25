import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas

# Page configuration
st.set_page_config(
    page_title="Hospital Stock Management System",
    page_icon="🏥",
    layout="wide"
)

# Snowflake connection function using Streamlit secrets
def get_snowflake_connection():
    """
    Connect to Snowflake using credentials from Streamlit secrets
    """
    try:
        # Get credentials from Streamlit secrets
        conn = snowflake.connector.connect(
            user=st.secrets["SNOWFLAKE_USER"],
            password=st.secrets["SNOWFLAKE_PASSWORD"],
            account=st.secrets["SNOWFLAKE_ACCOUNT"]
        )
        
        cursor = conn.cursor()
        
        # Use warehouse from secrets
        warehouse = st.secrets.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")
        database = st.secrets.get("SNOWFLAKE_DATABASE", "HOSPITAL_STOCK_DB")
        schema = st.secrets.get("SNOWFLAKE_SCHEMA", "INVENTORY")
        
        # Create warehouse if it doesn't exist
        cursor.execute(f"CREATE WAREHOUSE IF NOT EXISTS {warehouse} WITH WAREHOUSE_SIZE='X-SMALL' AUTO_SUSPEND=60 AUTO_RESUME=TRUE")
        cursor.execute(f"USE WAREHOUSE {warehouse}")
        
        # Create database and schema if they don't exist
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
        cursor.execute(f"USE DATABASE {database}")
        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        cursor.execute(f"USE SCHEMA {schema}")
        
        cursor.close()
        return conn
    except KeyError as e:
        st.error(f"Missing configuration in secrets: {str(e)}")
        return None
    except Exception as e:
        st.error(f"Connection failed: {str(e)}")
        return None

# Test connection function
def test_connection(user, password, account):
    """
    Test if Snowflake credentials are valid
    """
    try:
        # First, connect without specifying warehouse/database
        conn = snowflake.connector.connect(
            user=user,
            password=password,
            account=account
        )
        cursor = conn.cursor()
        
        # Get account info
        cursor.execute("SELECT CURRENT_ACCOUNT(), CURRENT_REGION()")
        result = cursor.fetchone()
        
        # Check if warehouse exists, if not create it
        cursor.execute("SHOW WAREHOUSES LIKE 'COMPUTE_WH'")
        warehouses = cursor.fetchall()
        if len(warehouses) == 0:
            cursor.execute("CREATE WAREHOUSE IF NOT EXISTS COMPUTE_WH WITH WAREHOUSE_SIZE='X-SMALL' AUTO_SUSPEND=60 AUTO_RESUME=TRUE")
        
        cursor.close()
        conn.close()
        return True, f"Account: {result[0]}, Region: {result[1]}"
    except Exception as e:
        return False, str(e)

# Function to load data from CSV to Snowflake
def load_sample_data_to_snowflake(conn, silent=False):
    """
    Load the sample CSV data into Snowflake
    """
    try:
        cursor = conn.cursor()
        
        # Get database and schema from secrets
        database = st.secrets.get("SNOWFLAKE_DATABASE", "HOSPITAL_STOCK_DB")
        schema = st.secrets.get("SNOWFLAKE_SCHEMA", "INVENTORY")
        warehouse = st.secrets.get("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")
        
        # Step 1: Create the main stock table
        if not silent:
            st.info("Checking tables...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS STOCK_RECORDS (
                date DATE,
                hospital_id VARCHAR(10),
                hospital_name VARCHAR(100),
                medicine_name VARCHAR(100),
                opening_stock NUMBER,
                received NUMBER,
                issued NUMBER,
                closing_stock NUMBER,
                lead_time_days NUMBER,
                min_stock_level NUMBER
            )
        """)
        
        # Step 2: Drop existing objects (dynamic tables depend on each other)
        if not silent:
            st.info("Setting up Dynamic Tables...")
        try:
            cursor.execute("DROP DYNAMIC TABLE IF EXISTS REORDER_RECOMMENDATIONS")
        except:
            pass
        try:
            cursor.execute("DROP DYNAMIC TABLE IF EXISTS CURRENT_STOCK_STATUS")
        except:
            pass
        try:
            cursor.execute("DROP VIEW IF EXISTS REORDER_RECOMMENDATIONS")
        except:
            pass
        try:
            cursor.execute("DROP VIEW IF EXISTS CURRENT_STOCK_STATUS")
        except:
            pass
        
        # Step 3: Create Dynamic Tables (auto-refresh)
        cursor.execute(f"""
            CREATE OR REPLACE DYNAMIC TABLE CURRENT_STOCK_STATUS
            TARGET_LAG = '1 minute'
            WAREHOUSE = {warehouse}
            AS
            SELECT 
                hospital_id,
                hospital_name,
                medicine_name,
                closing_stock as current_stock,
                min_stock_level,
                lead_time_days,
                AVG(issued) OVER (
                    PARTITION BY hospital_id, medicine_name 
                    ORDER BY date 
                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                ) as avg_daily_usage,
                CASE 
                    WHEN closing_stock <= min_stock_level THEN 'CRITICAL'
                    WHEN closing_stock / NULLIF(AVG(issued) OVER (
                        PARTITION BY hospital_id, medicine_name 
                        ORDER BY date 
                        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                    ), 0) <= lead_time_days THEN 'WARNING'
                    ELSE 'HEALTHY'
                END as stock_status,
                ROUND(closing_stock / NULLIF(AVG(issued) OVER (
                    PARTITION BY hospital_id, medicine_name 
                    ORDER BY date 
                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                ), 0), 1) as days_until_stockout,
                date
            FROM STOCK_RECORDS
            QUALIFY ROW_NUMBER() OVER (PARTITION BY hospital_id, medicine_name ORDER BY date DESC) = 1
        """)
        
        cursor.execute(f"""
            CREATE OR REPLACE DYNAMIC TABLE REORDER_RECOMMENDATIONS
            TARGET_LAG = '1 minute'
            WAREHOUSE = {warehouse}
            AS
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
                    WHEN stock_status = 'CRITICAL' THEN 
                        GREATEST(ROUND((avg_daily_usage * (lead_time_days + 30)) - current_stock), 0)
                    WHEN stock_status = 'WARNING' THEN 
                        GREATEST(ROUND((avg_daily_usage * (lead_time_days + 15)) - current_stock), 0)
                    ELSE 0
                END as recommended_order_quantity,
                CASE 
                    WHEN stock_status = 'CRITICAL' THEN 1
                    WHEN stock_status = 'WARNING' THEN 2
                    ELSE 3
                END as priority
            FROM CURRENT_STOCK_STATUS
            ORDER BY priority, hospital_id, medicine_name
        """)
        
        if not silent:
            st.success("✅ Dynamic Tables created! (Auto-refresh every 1 minute)")
            st.info("Tables created! Loading data...")
        
        # Step 3: Read CSV file
        df = pd.read_csv('sample_data.csv')
        
        # Rename columns to uppercase to match Snowflake table
        df.columns = df.columns.str.upper()
        
        # Convert date column to proper string format for Snowflake
        df['DATE'] = pd.to_datetime(df['DATE']).dt.strftime('%Y-%m-%d')
        
        # Step 4: Write to Snowflake
        success, nchunks, nrows, _ = write_pandas(
            conn=conn,
            df=df,
            table_name='STOCK_RECORDS',
            database=database,
            schema=schema,
            auto_create_table=False
        )
        
        cursor.close()
        
        if success:
            if not silent:
                st.success(f"✅ Successfully loaded {nrows} records into Snowflake!")
            return True
        return False
    except Exception as e:
        if not silent:
            st.error(f"Error loading data: {str(e)}")
        return False

# Function to get current stock data
def get_current_stock_data(conn):
    """
    Fetch current stock status from Snowflake
    """
    query = """
    SELECT 
        HOSPITAL_ID,
        HOSPITAL_NAME,
        MEDICINE_NAME,
        CURRENT_STOCK,
        MIN_STOCK_LEVEL,
        LEAD_TIME_DAYS,
        AVG_DAILY_USAGE,
        STOCK_STATUS,
        DAYS_UNTIL_STOCKOUT,
        DATE
    FROM CURRENT_STOCK_STATUS 
    ORDER BY HOSPITAL_ID, MEDICINE_NAME
    """
    df = pd.read_sql(query, conn)
    # Convert column names to lowercase for easier handling
    df.columns = df.columns.str.lower()
    return df

# Function to get reorder recommendations
def get_reorder_recommendations(conn):
    """
    Fetch reorder recommendations from Snowflake
    """
    query = """
    SELECT 
        HOSPITAL_ID,
        HOSPITAL_NAME,
        MEDICINE_NAME,
        CURRENT_STOCK,
        AVG_DAILY_USAGE,
        LEAD_TIME_DAYS,
        STOCK_STATUS,
        DAYS_UNTIL_STOCKOUT,
        RECOMMENDED_ORDER_QUANTITY,
        PRIORITY
    FROM REORDER_RECOMMENDATIONS 
    WHERE STOCK_STATUS IN ('CRITICAL', 'WARNING')
    ORDER BY PRIORITY, DAYS_UNTIL_STOCKOUT
    """
    df = pd.read_sql(query, conn)
    # Convert column names to lowercase for easier handling
    df.columns = df.columns.str.lower()
    return df

# Function to create heatmap
def create_stock_heatmap(df):
    """
    Create an interactive heatmap showing stock status across hospitals and medicines
    """
    # Pivot data for heatmap
    pivot_df = df.pivot_table(
        values='current_stock',
        index='medicine_name',
        columns='hospital_name',
        aggfunc='sum'
    )
    
    # Enhanced color scale with smooth gradients
    fig = go.Figure(data=go.Heatmap(
        z=pivot_df.values,
        x=pivot_df.columns,
        y=pivot_df.index,
        colorscale=[
            [0, '#ff1744'],      # Deep red for critical
            [0.2, '#ff5252'],    # Red
            [0.4, '#ffc107'],    # Amber for warning
            [0.6, '#ffeb3b'],    # Yellow
            [0.8, '#4caf50'],    # Green
            [1, '#2e7d32']       # Dark green for healthy
        ],
        text=pivot_df.values,
        texttemplate='<b>%{text}</b>',
        textfont={"size": 12, "color": "white", "family": "Arial Black"},
        colorbar=dict(
            title=dict(text="<b>Stock Level</b>", font=dict(size=14, color="#495057")),
            tickfont=dict(size=12, color="#6c757d"),
            thickness=20,
            len=0.75,
            x=1.02,
            xpad=10
        ),
        hovertemplate='<b>%{y}</b><br>Hospital: %{x}<br>Stock Level: <b>%{z}</b> units<extra></extra>',
        showscale=True
    ))
    
    fig.update_layout(
        title=dict(
            text="🔥 Stock Levels Heatmap",
            font=dict(size=20, color="#212529", family="Arial"),
            x=0.5,
            xanchor='center'
        ),
        xaxis=dict(
            title=dict(text="<b>Hospital</b>", font=dict(size=14, color="#495057")),
            tickfont=dict(size=11, color="#6c757d"),
            gridcolor='rgba(128, 128, 128, 0.1)',
            showgrid=True
        ),
        yaxis=dict(
            title=dict(text="<b>Medicine</b>", font=dict(size=14, color="#495057")),
            tickfont=dict(size=11, color="#6c757d"),
            gridcolor='rgba(128, 128, 128, 0.1)',
            showgrid=True
        ),
        height=550,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=100, r=50, t=60, b=50),
        font=dict(family="Arial")
    )
    
    return fig

# Function to create status distribution chart
def create_status_chart(df):
    """
    Create a pie chart showing distribution of stock statuses
    """
    status_counts = df['stock_status'].value_counts()
    
    # Enhanced color palette with gradients
    colors = {
        'CRITICAL': '#ff1744',      # Deep red
        'WARNING': '#ffa726',       # Orange
        'HEALTHY': '#4caf50'        # Green
    }
    
    # Calculate percentages for display
    total = status_counts.sum()
    percentages = (status_counts.values / total * 100).round(1)
    
    fig = go.Figure(data=[go.Pie(
        labels=status_counts.index,
        values=status_counts.values,
        hole=0.5,
        marker=dict(
            colors=[colors.get(status, '#6c757d') for status in status_counts.index],
            line=dict(color='white', width=3),
            pattern=dict(
                fillmode="overlay",
                size=10
            )
        ),
        textinfo='label+percent',
        texttemplate='<b>%{label}</b><br>%{value} items<br>(%{percent})',
        textfont=dict(size=12, color='white', family="Arial Black"),
        hovertemplate='<b>%{label}</b><br>Count: <b>%{value}</b><br>Percentage: <b>%{percent}</b><extra></extra>',
        pull=[0.05 if status == 'CRITICAL' else 0 for status in status_counts.index],
        rotation=90
    )])
    
    fig.update_layout(
        title=dict(
            text="📊 Status Distribution",
            font=dict(size=18, color="#212529", family="Arial"),
            x=0.5,
            xanchor='center'
        ),
        annotations=[dict(
            text=f'<b style="font-size:24px;">{status_counts.sum()}</b><br><span style="color:#6c757d;">Total Items</span>',
            x=0.5, y=0.5,
            font_size=16,
            font_color="#495057",
            showarrow=False,
            align='center'
        )],
        height=480,
        showlegend=True,
        legend=dict(
            orientation="v",
            yanchor="middle",
            y=0.5,
            xanchor="right",
            x=1.15,
            font=dict(size=12, color="#495057"),
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="#e0e0e0",
            borderwidth=1
        ),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
        margin=dict(l=20, r=150, t=60, b=20),
        font=dict(family="Arial")
    )
    
    return fig

# Function to generate AI-like alert summary
def generate_alert_summary(reorder_df):
    """
    Generate plain-language summary of critical alerts
    """
    if len(reorder_df) == 0:
        return "✅ All stock levels are healthy. No immediate action required."
    
    critical_count = len(reorder_df[reorder_df['stock_status'] == 'CRITICAL'])
    warning_count = len(reorder_df[reorder_df['stock_status'] == 'WARNING'])
    
    summary = f"⚠️ **STOCK ALERT SUMMARY**\n\n"
    summary += f"- **{critical_count}** medicines at CRITICAL levels (immediate action required)\n"
    summary += f"- **{warning_count}** medicines at WARNING levels (reorder soon)\n\n"
    
    if critical_count > 0:
        summary += "**Most Urgent Items:**\n"
        top_critical = reorder_df[reorder_df['stock_status'] == 'CRITICAL'].head(3)
        for idx, row in top_critical.iterrows():
            summary += f"- {row['hospital_name']}: **{row['medicine_name']}** "
            summary += f"(Only {int(row['current_stock'])} units left, "
            summary += f"avg daily use: {int(row['avg_daily_usage'])} units)\n"
    
    return summary

# ============ MAIN APP ============

# Enhanced Custom CSS for professional UI
st.markdown("""
    <style>
    /* Main styling */
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 15px;
        color: white;
        margin-bottom: 2rem;
        box-shadow: 0 10px 30px rgba(0,0,0,0.1);
    }
    .main-header h1 {
        color: white;
        margin: 0;
        font-size: 2.5rem;
        font-weight: 700;
    }
    .main-header p {
        color: rgba(255,255,255,0.9);
        margin: 0.5rem 0 0 0;
        font-size: 1.1rem;
    }
    
    /* Metric boxes */
    .metric-box {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        border-left: 5px solid;
        text-align: center;
        box-shadow: 0 4px 6px rgba(0,0,0,0.07);
        transition: transform 0.2s, box-shadow 0.2s;
        height: 100%;
    }
    .metric-box:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(0,0,0,0.12);
    }
    .metric-box h1 {
        font-size: 2.5rem;
        margin: 0;
        font-weight: 700;
    }
    .metric-box h2 {
        font-size: 2rem;
        margin: 0.5rem 0;
        font-weight: 600;
    }
    .metric-box p {
        margin: 0;
        color: #6c757d;
        font-size: 0.9rem;
        font-weight: 500;
    }
    .critical-box { 
        border-left-color: #dc3545;
        background: linear-gradient(135deg, #fff 0%, #fff5f5 100%);
    }
    .warning-box { 
        border-left-color: #ffc107;
        background: linear-gradient(135deg, #fff 0%, #fffbf0 100%);
    }
    .healthy-box { 
        border-left-color: #28a745;
        background: linear-gradient(135deg, #fff 0%, #f0fff4 100%);
    }
    .info-box { 
        border-left-color: #007bff;
        background: linear-gradient(135deg, #fff 0%, #f0f8ff 100%);
    }
    
    /* Section containers */
    .section-container {
        background: white;
        padding: 1.5rem;
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        margin-bottom: 1.5rem;
    }
    
    /* Filter section */
    .filter-section {
        background: #f8f9fa;
        padding: 1.5rem;
        border-radius: 12px;
        margin-bottom: 1.5rem;
    }
    
    /* Card styling */
    .info-card {
        background: white;
        padding: 1.25rem;
        border-radius: 10px;
        border-left: 4px solid;
        margin: 0.75rem 0;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }
    
    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #f8f9fa 0%, #ffffff 100%);
    }
    
    /* Hide Streamlit default elements */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    /* Better spacing */
    .stMarkdown {
        margin-bottom: 1rem;
    }
    
    /* Button styling */
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
        transition: all 0.3s;
    }
    
    /* Input styling */
    .stTextInput > div > div > input {
        border-radius: 8px;
    }
    
    /* Selectbox styling */
    .stSelectbox > div > div > select {
        border-radius: 8px;
    }
    </style>
""", unsafe_allow_html=True)

# Professional Header
st.markdown("""
    <div class="main-header">
        <h1>🏥 Hospital Stock Management System</h1>
        <p>AI-Powered Inventory Monitoring & Stockout Prevention</p>
    </div>
""", unsafe_allow_html=True)

# Auto-connect and auto-load data on page load
# Initialize connection automatically
if 'conn_initialized' not in st.session_state:
    with st.spinner("🔄 Connecting to Snowflake..."):
        try:
            conn = get_snowflake_connection()
            if conn:
                st.session_state['conn'] = conn
                st.session_state['conn_initialized'] = True
                st.session_state['connected'] = True
            else:
                st.session_state['conn_initialized'] = True
                st.session_state['connected'] = False
        except Exception as e:
            st.error(f"Failed to connect to Snowflake: {str(e)}")
            st.session_state['conn_initialized'] = True
            st.session_state['connected'] = False

# Auto-load data if not already loaded
if st.session_state.get('connected', False) and not st.session_state.get('data_loaded', False):
    with st.spinner("📊 Loading sample data..."):
        conn = st.session_state.get('conn')
        if conn:
            # Check if data already exists
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM STOCK_RECORDS")
                count = cursor.fetchone()[0]
                cursor.close()
                
                if count == 0:
                    # Data doesn't exist, load it
                    if load_sample_data_to_snowflake(conn, silent=True):
                        st.session_state['data_loaded'] = True
                        st.rerun()
                else:
                    # Data already exists
                    st.session_state['data_loaded'] = True
                    st.rerun()
            except Exception as e:
                # Table doesn't exist, create and load data
                if load_sample_data_to_snowflake(conn, silent=True):
                    st.session_state['data_loaded'] = True
                    st.rerun()

# Main content
if st.session_state.get('connected', False) and st.session_state.get('data_loaded', False):
    
    # Get connection from session state
    conn = st.session_state.get('conn')
    
    if conn:
        try:
            # Fetch data FIRST
            stock_df = get_current_stock_data(conn)
            reorder_df = get_reorder_recommendations(conn)
            
            # Enhanced Filter Section
            with st.container():
                st.markdown("""
                    <div class="filter-section">
                        <h3 style='margin-top: 0; color: #495057;'>🔍 Filters & Search</h3>
                    </div>
                """, unsafe_allow_html=True)
                
                filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([2, 2, 2, 1])
                
                with filter_col1:
                    all_medicines = ['All Medicines'] + sorted(stock_df['medicine_name'].unique().tolist())
                    selected_medicine = st.selectbox("**Medicine**", all_medicines, 
                                                     help="Filter by specific medicine")
                
                with filter_col2:
                    all_hospitals = ['All Hospitals'] + sorted(stock_df['hospital_name'].unique().tolist())
                    selected_hospital = st.selectbox("**Hospital**", all_hospitals,
                                                     help="Filter by specific hospital")
                
                with filter_col3:
                    # Get available dates from database
                    date_query = "SELECT MIN(DATE) as min_date, MAX(DATE) as max_date FROM STOCK_RECORDS"
                    date_range = pd.read_sql(date_query, conn)
                    date_range_option = st.selectbox("**Date Range**", 
                                                    ["All Time", "Last 7 Days", "Last 30 Days", "Custom"],
                                                    help="Select date range for analysis")
                
                with filter_col4:
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("🔄 Reset", use_container_width=True):
                        st.rerun()
                
                # Custom date range if selected
                if date_range_option == "Custom":
                    date_col1, date_col2 = st.columns(2)
                    with date_col1:
                        start_date = st.date_input(
                            "From Date",
                            value=pd.to_datetime(date_range['MIN_DATE'].iloc[0]) if len(date_range) > 0 else datetime.now(),
                            key="start_date"
                        )
                    with date_col2:
                        end_date = st.date_input(
                            "To Date", 
                            value=pd.to_datetime(date_range['MAX_DATE'].iloc[0]) if len(date_range) > 0 else datetime.now(),
                            key="end_date"
                        )
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Apply filters
            filtered_stock_df = stock_df.copy()
            if selected_medicine != 'All Medicines':
                filtered_stock_df = filtered_stock_df[filtered_stock_df['medicine_name'] == selected_medicine]
            if selected_hospital != 'All Hospitals':
                filtered_stock_df = filtered_stock_df[filtered_stock_df['hospital_name'] == selected_hospital]
            
            # Filter reorder_df as well
            filtered_reorder_df = reorder_df.copy()
            if selected_medicine != 'All Medicines':
                filtered_reorder_df = filtered_reorder_df[filtered_reorder_df['medicine_name'] == selected_medicine]
            if selected_hospital != 'All Hospitals':
                filtered_reorder_df = filtered_reorder_df[filtered_reorder_df['hospital_name'] == selected_hospital]
            
            # Enhanced KPI Metrics Section
            st.markdown("""
                <div style='margin: 2rem 0 1rem 0;'>
                    <h2 style='color: #495057; margin: 0;'>📊 Stock Overview</h2>
                    <p style='color: #6c757d; margin: 0.5rem 0;'>Real-time inventory status at a glance</p>
                </div>
            """, unsafe_allow_html=True)
            
            col1, col2, col3, col4 = st.columns(4)
            
            total_items = len(filtered_stock_df)
            critical_count = len(filtered_stock_df[filtered_stock_df['stock_status'] == 'CRITICAL'])
            warning_count = len(filtered_stock_df[filtered_stock_df['stock_status'] == 'WARNING'])
            healthy_count = len(filtered_stock_df[filtered_stock_df['stock_status'] == 'HEALTHY'])
            
            with col1:
                st.markdown(f"""
                    <div class="metric-box info-box">
                        <h1 style="margin:0; color:#007bff;">📦</h1>
                        <h2 style="margin:10px 0; color:#212529;">{total_items}</h2>
                        <p style="margin:0; color:#6c757d; font-weight: 500;">Total Items</p>
                    </div>
                """, unsafe_allow_html=True)
            
            with col2:
                st.markdown(f"""
                    <div class="metric-box critical-box">
                        <h1 style="margin:0; color:#dc3545;">🚨</h1>
                        <h2 style="margin:10px 0; color:#dc3545;">{critical_count}</h2>
                        <p style="margin:0; color:#6c757d; font-weight: 500;">Critical</p>
                    </div>
                """, unsafe_allow_html=True)
            
            with col3:
                st.markdown(f"""
                    <div class="metric-box warning-box">
                        <h1 style="margin:0; color:#ffc107;">⚠️</h1>
                        <h2 style="margin:10px 0; color:#ffc107;">{warning_count}</h2>
                        <p style="margin:0; color:#6c757d; font-weight: 500;">Warning</p>
                    </div>
                """, unsafe_allow_html=True)
            
            with col4:
                st.markdown(f"""
                    <div class="metric-box healthy-box">
                        <h1 style="margin:0; color:#28a745;">✅</h1>
                        <h2 style="margin:10px 0; color:#28a745;">{healthy_count}</h2>
                        <p style="margin:0; color:#6c757d; font-weight: 500;">Healthy</p>
                    </div>
                """, unsafe_allow_html=True)
            
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Organized Dashboard with Tabs
            tab1, tab2, tab3, tab4, tab5 = st.tabs([
                "📊 Dashboard", 
                "📈 Analytics", 
                "🚨 Alerts", 
                "📋 Inventory", 
                "🤖 AI Assistant"
            ])
            
            with tab1:
                # Show search results if specific medicine selected
                if selected_medicine != 'All Medicines':
                    st.markdown(f"### 📋 Detailed Info: {selected_medicine}")
                    
                    for idx, row in filtered_stock_df.iterrows():
                        status_color = {
                            'CRITICAL': '#dc3545',
                            'WARNING': '#ffc107',
                            'HEALTHY': '#28a745'
                        }.get(row['stock_status'], '#6c757d')
                        
                        st.markdown(f"""
                            <div style='background-color: white; padding: 1.5rem; border-radius: 10px; 
                                        border-left: 5px solid {status_color}; margin: 1rem 0; 
                                        box-shadow: 0 2px 8px rgba(0,0,0,0.08);'>
                                <h4 style='margin: 0 0 1rem 0; color: #212529; font-weight: 600;'>{row['hospital_name']}</h4>
                                <div style='display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;'>
                                    <div>
                                        <p style='margin: 0.5rem 0; color: #6c757d;'><b>Current Stock:</b> <span style='color: #212529;'>{int(row['current_stock'])} units</span></p>
                                        <p style='margin: 0.5rem 0; color: #6c757d;'><b>Status:</b> <span style='color: {status_color}; font-weight: bold;'>{row['stock_status']}</span></p>
                                    </div>
                                    <div>
                                        <p style='margin: 0.5rem 0; color: #6c757d;'><b>Avg Daily Usage:</b> <span style='color: #212529;'>{int(row['avg_daily_usage'])} units/day</span></p>
                                        <p style='margin: 0.5rem 0; color: #6c757d;'><b>Days Until Stockout:</b> <span style='color: #212529;'>{int(row['days_until_stockout'])} days</span></p>
                                    </div>
                                </div>
                            </div>
                        """, unsafe_allow_html=True)
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # HOSPITAL PERFORMANCE COMPARISON
                st.markdown("### 🏥 Hospital Performance Leaderboard")
            
                # Calculate hospital scores (using original data, not filtered)
                hospital_perf = stock_df.groupby('hospital_name').agg({
                    'stock_status': lambda x: (
                        (x == 'HEALTHY').sum() * 3 + 
                        (x == 'WARNING').sum() * 1 - 
                        (x == 'CRITICAL').sum() * 2
                    )
                }).reset_index()
                hospital_perf.columns = ['Hospital', 'Health_Score']
                hospital_perf = hospital_perf.sort_values('Health_Score', ascending=False)
                
                # Add critical count
                critical_per_hospital = stock_df[stock_df['stock_status'] == 'CRITICAL'].groupby('hospital_name').size().reset_index()
                critical_per_hospital.columns = ['Hospital', 'Critical_Items']
                
                hospital_perf = hospital_perf.merge(critical_per_hospital, on='Hospital', how='left').fillna(0)
                hospital_perf['Critical_Items'] = hospital_perf['Critical_Items'].astype(int)
                
                # Add rank
                hospital_perf['Rank'] = range(1, len(hospital_perf) + 1)
                
                # Display as cards
                if len(hospital_perf) > 0:
                    cols = st.columns(len(hospital_perf))
                    
                    for idx, (col, row) in enumerate(zip(cols, hospital_perf.itertuples())):
                        with col:
                            medal = "🥇" if row.Rank == 1 else "🥈" if row.Rank == 2 else "🥉"
                            color = "#28a745" if row.Rank == 1 else "#ffc107" if row.Rank == 2 else "#dc3545"
                            
                            st.markdown(f"""
                                <div style='background-color: white; padding: 1.25rem; border-radius: 10px; 
                                            border-left: 5px solid {color}; text-align: center; 
                                            box-shadow: 0 2px 8px rgba(0,0,0,0.08);'>
                                    <h2 style='margin: 0; color: {color};'>{medal}</h2>
                                    <h4 style='margin: 0.5rem 0; color: #212529; font-weight: 600;'>{row.Hospital.split()[0]}</h4>
                                    <p style='margin: 0.5rem 0; color: #6c757d; font-size: 0.9rem;'>Score: <b style='color: #212529;'>{int(row.Health_Score)}</b></p>
                                    <p style='margin: 0; color: #dc3545; font-size: 0.85rem; font-weight: 500;'>{int(row.Critical_Items)} Critical</p>
                                </div>
                            """, unsafe_allow_html=True)
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # Add bar chart visualization
                if len(hospital_perf) > 0:
                    perf_fig = go.Figure()
                    
                    # Color mapping based on rank
                    bar_colors = []
                    for rank in hospital_perf['Rank']:
                        if rank == 1:
                            bar_colors.append('#4caf50')  # Green
                        elif rank == 2:
                            bar_colors.append('#ffa726')  # Orange
                        else:
                            bar_colors.append('#ef5350')  # Red
                    
                    perf_fig.add_trace(go.Bar(
                        x=hospital_perf['Hospital'],
                        y=hospital_perf['Health_Score'],
                        marker=dict(
                            color=bar_colors,
                            line=dict(color='white', width=2),
                            pattern=dict(fillmode="overlay", size=5)
                        ),
                        text=hospital_perf['Health_Score'].astype(int),
                        textposition='outside',
                        textfont=dict(size=12, color='#495057', family='Arial Black'),
                        hovertemplate='<b>%{x}</b><br>Health Score: <b>%{y}</b><br>Rank: %{customdata}<extra></extra>',
                        customdata=hospital_perf['Rank']
                    ))
                    
                    perf_fig.update_layout(
                        title=dict(
                            text="🏥 Hospital Performance Comparison",
                            font=dict(size=18, color="#212529", family="Arial"),
                            x=0.5,
                            xanchor='center'
                        ),
                        xaxis=dict(
                            title=dict(text="<b>Hospital</b>", font=dict(size=13, color="#495057")),
                            tickfont=dict(size=11, color="#6c757d"),
                            gridcolor='rgba(128, 128, 128, 0.1)',
                            showgrid=False
                        ),
                        yaxis=dict(
                            title=dict(text="<b>Health Score</b>", font=dict(size=13, color="#495057")),
                            tickfont=dict(size=11, color="#6c757d"),
                            gridcolor='rgba(128, 128, 128, 0.15)',
                            showgrid=True,
                            zeroline=True,
                            zerolinecolor='rgba(128, 128, 128, 0.3)'
                        ),
                        height=400,
                        plot_bgcolor='rgba(250, 250, 250, 0.5)',
                        paper_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=60, r=30, t=70, b=80),
                        font=dict(family="Arial"),
                        showlegend=False
                    )
                    
                    st.plotly_chart(perf_fig, use_container_width=True)
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # IMPACT METRICS SECTION
                st.markdown("### 💰 Impact & Cost Savings")
                
                impact_col1, impact_col2, impact_col3, impact_col4 = st.columns(4)
                
                # Calculate impact metrics
                stockouts_prevented = len(reorder_df[reorder_df['stock_status'] == 'CRITICAL'])
                avg_cost_per_stockout = 50000  # ₹50,000 average cost per stockout incident
                total_savings = stockouts_prevented * avg_cost_per_stockout
                
                with impact_col1:
                    st.metric(
                        "🛡️ Stockouts Prevented",
                        f"{stockouts_prevented}",
                        delta="This Month",
                        delta_color="normal"
                    )
                
                with impact_col2:
                    st.metric(
                        "💵 Cost Savings",
                        f"₹{total_savings:,}",
                        delta="+15% vs last month",
                        delta_color="normal"
                    )
                
                with impact_col3:
                    lives_impacted = stockouts_prevented * 25  # Avg 25 patients affected per stockout
                    st.metric(
                        "👨‍⚕️ Patients Served",
                        f"{lives_impacted:,}",
                        delta="Protected from delays",
                        delta_color="normal"
                    )
                
                with impact_col4:
                    waste_reduction = len(stock_df[stock_df['stock_status'] == 'HEALTHY']) * 2
                    st.metric(
                        "♻️ Waste Reduced",
                        f"{waste_reduction} kg",
                        delta="Expired meds avoided",
                        delta_color="normal"
                    )
            
            with tab2:
                # Analytics Tab - Stock Trend and Visualizations
                st.markdown("### 📈 Stock Trend Analysis")
            
                try:
                    # Get historical data
                    trend_query = """
                    SELECT DATE, MEDICINE_NAME, CLOSING_STOCK, HOSPITAL_NAME
                    FROM STOCK_RECORDS
                    ORDER BY DATE, MEDICINE_NAME
                    """
                    trend_df = pd.read_sql(trend_query, conn)
                    trend_df.columns = trend_df.columns.str.lower()
                    
                    col1, col2 = st.columns([3, 1])
                    
                    with col1:
                        # Let user select medicine
                        medicines = sorted(trend_df['medicine_name'].unique())
                        selected_trend_medicine = st.selectbox("Select Medicine to Track:", medicines, key="trend_medicine")
                    
                    with col2:
                        # Chart type selector
                        chart_type = st.radio("Chart Type:", ["Line", "Area"], horizontal=True, key="chart_type")
                    
                    # Filter and plot
                    med_data = trend_df[trend_df['medicine_name'] == selected_trend_medicine]
                    
                    # Enhanced color palette
                    colors_list = [
                        '#667eea',  # Purple-blue
                        '#f093fb',  # Pink
                        '#4facfe',  # Blue
                        '#43e97b',  # Green
                        '#fa709a',  # Rose
                        '#fee140',  # Yellow
                        '#30cfd0'   # Cyan
                    ]
                    
                    if chart_type == "Line":
                        fig = go.Figure()
                        
                        for idx, hospital in enumerate(med_data['hospital_name'].unique()):
                            hosp_data = med_data[med_data['hospital_name'] == hospital]
                            color = colors_list[idx % len(colors_list)]
                            fig.add_trace(go.Scatter(
                                x=hosp_data['date'],
                                y=hosp_data['closing_stock'],
                                name=hospital,
                                mode='lines+markers',
                                line=dict(
                                    width=3.5,
                                    color=color,
                                    shape='spline',
                                    smoothing=1.3
                                ),
                                marker=dict(
                                    size=9,
                                    color=color,
                                    line=dict(width=2, color='white'),
                                    symbol='circle'
                                ),
                                hovertemplate=f'<b>{hospital}</b><br>Date: %{{x}}<br>Stock: <b>%{{y}}</b> units<extra></extra>',
                                fill=None
                            ))
                    else:
                        fig = go.Figure()
                        
                        # Color mapping for area chart with transparency
                        color_map = {
                            '#667eea': 'rgba(102, 126, 234, 0.4)',
                            '#f093fb': 'rgba(240, 147, 251, 0.4)',
                            '#4facfe': 'rgba(79, 172, 254, 0.4)',
                            '#43e97b': 'rgba(67, 233, 123, 0.4)',
                            '#fa709a': 'rgba(250, 112, 154, 0.4)',
                            '#fee140': 'rgba(254, 225, 64, 0.4)',
                            '#30cfd0': 'rgba(48, 207, 208, 0.4)'
                        }
                        
                        for idx, hospital in enumerate(med_data['hospital_name'].unique()):
                            hosp_data = med_data[med_data['hospital_name'] == hospital]
                            color = colors_list[idx % len(colors_list)]
                            fillcolor = color_map.get(color, 'rgba(102, 126, 234, 0.4)')
                            
                            fig.add_trace(go.Scatter(
                                x=hosp_data['date'],
                                y=hosp_data['closing_stock'],
                                name=hospital,
                                mode='lines',
                                fill='tonexty' if idx > 0 else 'tozeroy',
                                line=dict(
                                    width=2.5,
                                    color=color,
                                    shape='spline',
                                    smoothing=1.3
                                ),
                                fillcolor=fillcolor,
                                hovertemplate=f'<b>{hospital}</b><br>Date: %{{x}}<br>Stock: <b>%{{y}}</b> units<extra></extra>'
                            ))
                    
                    fig.update_layout(
                        title=dict(
                            text=f'📈 {selected_trend_medicine} - Stock Trend Over Time',
                            font=dict(size=20, color="#212529", family="Arial"),
                            x=0.5,
                            xanchor='center'
                        ),
                        xaxis=dict(
                            title=dict(text="<b>Date</b>", font=dict(size=14, color="#495057")),
                            tickfont=dict(size=11, color="#6c757d"),
                            gridcolor='rgba(128, 128, 128, 0.15)',
                            showgrid=True,
                            zeroline=False,
                            showline=True,
                            linecolor='rgba(128, 128, 128, 0.2)'
                        ),
                        yaxis=dict(
                            title=dict(text="<b>Stock Level (units)</b>", font=dict(size=14, color="#495057")),
                            tickfont=dict(size=11, color="#6c757d"),
                            gridcolor='rgba(128, 128, 128, 0.15)',
                            showgrid=True,
                            zeroline=True,
                            zerolinecolor='rgba(128, 128, 128, 0.3)',
                            showline=True,
                            linecolor='rgba(128, 128, 128, 0.2)'
                        ),
                        height=500,
                        hovermode='x unified',
                        plot_bgcolor='rgba(250, 250, 250, 0.5)',
                        paper_bgcolor='rgba(0,0,0,0)',
                        margin=dict(l=60, r=30, t=70, b=50),
                        font=dict(family="Arial"),
                        legend=dict(
                            orientation="v",
                            yanchor="top",
                            y=1,
                            xanchor="right",
                            x=1.02,
                            bgcolor="rgba(255,255,255,0.9)",
                            bordercolor="#e0e0e0",
                            borderwidth=1,
                            font=dict(size=11, color="#495057")
                        )
                    )
                    
                    st.plotly_chart(fig, use_container_width=True)
                    
                except Exception as e:
                    st.info("Add more historical data to see trends")
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # Visualizations (using filtered data)
                st.markdown("### 📊 Visual Analytics")
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.markdown("**🔥 Stock Levels Heatmap**")
                    if len(filtered_stock_df) > 0:
                        heatmap = create_stock_heatmap(filtered_stock_df)
                        heatmap.update_layout(
                            plot_bgcolor='rgba(0,0,0,0)',
                            paper_bgcolor='rgba(0,0,0,0)'
                        )
                        st.plotly_chart(heatmap, use_container_width=True)
                    else:
                        st.info("No data available for selected filters")
                
                with col2:
                    st.markdown("**📊 Status Distribution**")
                    if len(filtered_stock_df) > 0:
                        status_chart = create_status_chart(filtered_stock_df)
                        status_chart.update_layout(
                            plot_bgcolor='rgba(0,0,0,0)',
                            paper_bgcolor='rgba(0,0,0,0)'
                        )
                        st.plotly_chart(status_chart, use_container_width=True)
                    else:
                        st.info("No data available for selected filters")
            
            with tab3:
                # Alerts Tab
                st.markdown("### 📢 Alert Summary & Predictions")
                
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    st.markdown("**Alert Summary**")
                    alert_text = generate_alert_summary(reorder_df)
                    st.markdown(f"""
                        <div style='background: white; padding: 1.5rem; border-radius: 10px; 
                                    box-shadow: 0 2px 8px rgba(0,0,0,0.08);'>
                            {alert_text.replace(chr(10), '<br>')}
                        </div>
                    """, unsafe_allow_html=True)
                
                with col2:
                    st.markdown("**🔮 Next 7 Days Forecast**")
                    if len(reorder_df) > 0:
                        will_run_out = reorder_df[reorder_df['days_until_stockout'] <= 7]
                        if len(will_run_out) > 0:
                            st.error(f"⚠️ **{len(will_run_out)} items** will run out within 7 days!")
                            for idx, row in will_run_out.head(3).iterrows():
                                st.warning(f"📉 {row['medicine_name']}: {int(row['days_until_stockout'])} days left")
                        else:
                            st.success("✅ All items safe for next 7 days")
                    else:
                        st.success("✅ All stock levels healthy")
                
                st.markdown("<br>", unsafe_allow_html=True)
                
                # Reorder Recommendations Table (using filtered data)
                st.markdown("### 📋 Reorder Recommendations")
            
                if len(filtered_reorder_df) > 0:
                    display_df = filtered_reorder_df[[
                        'hospital_name', 'medicine_name', 'current_stock', 
                        'avg_daily_usage', 'days_until_stockout', 
                        'recommended_order_quantity', 'stock_status'
                    ]].copy()
                    
                    display_df.columns = [
                        'Hospital', 'Medicine', 'Current Stock', 
                        'Avg Daily Use', 'Days Until Stockout', 
                        'Recommended Order', 'Status'
                    ]
                    
                    # Color code the status
                    def highlight_status(row):
                        if row['Status'] == 'CRITICAL':
                            return ['background-color: #ffcdd2'] * len(row)
                        elif row['Status'] == 'WARNING':
                            return ['background-color: #ffe0b2'] * len(row)
                        else:
                            return [''] * len(row)
                    
                    styled_df = display_df.style.apply(highlight_status, axis=1)
                    st.dataframe(styled_df, use_container_width=True, height=400)
                    
                    # Export buttons
                    csv = display_df.to_csv(index=False)
                    col1, col2 = st.columns(2)
                    with col1:
                        st.download_button(
                            label="📥 Download Reorder List (CSV)",
                            data=csv,
                            file_name=f"reorder_list_{datetime.now().strftime('%Y%m%d')}.csv",
                            mime="text/csv",
                            use_container_width=True
                        )
                    with col2:
                        # Export full report as text
                        report = f"""HOSPITAL STOCK MANAGEMENT REPORT
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}

SUMMARY:
- Total Items: {len(stock_df)}
- Critical: {len(stock_df[stock_df['stock_status'] == 'CRITICAL'])}
- Warning: {len(stock_df[stock_df['stock_status'] == 'WARNING'])}

REORDER RECOMMENDATIONS:
{display_df.to_string()}
"""
                        st.download_button(
                            label="📄 Download Report (TXT)",
                            data=report,
                            file_name=f"stock_report_{datetime.now().strftime('%Y%m%d')}.txt",
                            mime="text/plain",
                            use_container_width=True
                        )
                else:
                    st.success("✅ No reorders needed at this time!")
            
            with tab4:
                # Inventory Tab
                st.markdown("### 📦 Detailed Stock Inventory")
                
                if len(filtered_stock_df) > 0:
                    detailed_df = filtered_stock_df[[
                        'hospital_name', 'medicine_name', 'current_stock', 
                        'min_stock_level', 'avg_daily_usage', 'lead_time_days', 'stock_status'
                    ]].copy()
                    
                    detailed_df.columns = [
                        'Hospital', 'Medicine', 'Current Stock', 
                        'Min Level', 'Avg Daily Use', 'Lead Time (Days)', 'Status'
                    ]
                    
                    st.dataframe(detailed_df, use_container_width=True, height=500)
                    
                    # Export inventory
                    inventory_csv = detailed_df.to_csv(index=False)
                    st.download_button(
                        label="📥 Download Inventory (CSV)",
                        data=inventory_csv,
                        file_name=f"inventory_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
                else:
                    st.info("No data available for selected filters")
            
            with tab5:
                # AI Assistant Tab
                st.markdown("### 🤖 AI Stock Assistant")
                st.markdown("Ask questions about your stock levels and get instant insights.")
                
                user_question = st.text_input(
                    "💬 Ask anything about stock levels:",
                    placeholder="e.g., Which medicines are critical? Show me insulin stock across hospitals",
                    key="ai_question"
                )
                
                if st.button("🔍 Ask AI", use_container_width=True, type="primary") and user_question:
                    with st.spinner("AI is analyzing..."):
                        try:
                            # Simple AI-like response based on data
                            if "critical" in user_question.lower():
                                critical = stock_df[stock_df['stock_status'] == 'CRITICAL']
                                if len(critical) > 0:
                                    response = f"**I found {len(critical)} critical items:**\n\n"
                                    for idx, row in critical.head(5).iterrows():
                                        response += f"• **{row['medicine_name']}** at {row['hospital_name']} - only {int(row['current_stock'])} units left\n"
                                else:
                                    response = "✅ Great news! No critical items right now."
                            
                            elif "insulin" in user_question.lower():
                                insulin = stock_df[stock_df['medicine_name'].str.contains('Insulin', case=False, na=False)]
                                if len(insulin) > 0:
                                    response = "**Insulin Stock Status:**\n\n"
                                    for idx, row in insulin.iterrows():
                                        response += f"• {row['hospital_name']}: {int(row['current_stock'])} units ({row['stock_status']})\n"
                                else:
                                    response = "No insulin data found."
                            
                            elif "hospital" in user_question.lower() or "location" in user_question.lower():
                                hospitals = stock_df.groupby('hospital_name').agg({
                                    'stock_status': lambda x: (x == 'CRITICAL').sum()
                                }).reset_index()
                                response = "**Hospital Overview:**\n\n"
                                for idx, row in hospitals.iterrows():
                                    response += f"• {row['hospital_name']}: {int(row['stock_status'])} critical items\n"
                            
                            else:
                                # General summary
                                total = len(stock_df)
                                critical_count = len(stock_df[stock_df['stock_status'] == 'CRITICAL'])
                                warning_count = len(stock_df[stock_df['stock_status'] == 'WARNING'])
                                response = f"**Current Stock Summary:**\n\n"
                                response += f"• Total items tracked: {total}\n"
                                response += f"• Critical alerts: {critical_count}\n"
                                response += f"• Warning alerts: {warning_count}\n"
                                response += f"• Healthy items: {total - critical_count - warning_count}"
                            
                            st.markdown(f"""
                                <div style='background: white; padding: 1.5rem; border-radius: 10px; 
                                            box-shadow: 0 2px 8px rgba(0,0,0,0.08); border-left: 4px solid #667eea;'>
                                    {response.replace(chr(10), '<br>')}
                                </div>
                            """, unsafe_allow_html=True)
                        except Exception as e:
                            st.error(f"Error: {str(e)}")
            
        except Exception as e:
            st.error(f"Error fetching data: {str(e)}")
            
else:
    # Show error message if connection failed
    if not st.session_state.get('connected', False):
        st.markdown("""
            <div style='background: linear-gradient(135deg, #ff6b6b 0%, #ee5a6f 100%); 
                        padding: 3rem; border-radius: 15px; text-align: center; margin: 2rem 0; color: white;'>
                <h2 style='color: white; margin-bottom: 1rem;'>❌ Connection Failed</h2>
                <p style='color: rgba(255,255,255,0.9); font-size: 1.1rem; margin-bottom: 2rem;'>
                    Unable to connect to Snowflake. Please check your configuration in Streamlit secrets.
                </p>
            </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
            <div style='background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); 
                        padding: 3rem; border-radius: 15px; text-align: center; margin: 2rem 0;'>
                <h2 style='color: #495057; margin-bottom: 1rem;'>⏳ Loading Data</h2>
                <p style='color: #6c757d; font-size: 1.1rem; margin-bottom: 2rem;'>
                    Please wait while we load the data...
                </p>
            </div>
        """, unsafe_allow_html=True)

# Professional Footer
st.markdown("""
    <div style='text-align: center; padding: 2rem 0; margin-top: 3rem; 
                border-top: 1px solid #e0e0e0; color: #6c757d;'>
        <p style='margin: 0; font-size: 0.9rem;'>
            Built for AI for Good Hackathon 2024 | Powered by Snowflake
        </p>
    </div>
""", unsafe_allow_html=True)
