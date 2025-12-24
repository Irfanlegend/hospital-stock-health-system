-- Step 1: Create Database and Schema
CREATE DATABASE IF NOT EXISTS HOSPITAL_STOCK_DB;
USE DATABASE HOSPITAL_STOCK_DB;
CREATE SCHEMA IF NOT EXISTS INVENTORY;
USE SCHEMA INVENTORY;

-- Step 2: Create the main stock table
CREATE OR REPLACE TABLE STOCK_RECORDS (
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
);

-- Step 3: Create a view for current stock status
CREATE OR REPLACE VIEW CURRENT_STOCK_STATUS AS
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
QUALIFY ROW_NUMBER() OVER (PARTITION BY hospital_id, medicine_name ORDER BY date DESC) = 1;

-- Step 4: Create a table for reorder recommendations
CREATE OR REPLACE VIEW REORDER_RECOMMENDATIONS AS
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
ORDER BY priority, hospital_id, medicine_name;