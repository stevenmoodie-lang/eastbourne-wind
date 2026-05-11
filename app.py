import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
from suntime import Sun
import pytz

# --- CONFIGURATION & CONSTANTS ---
LAT, LON = -41.29, 174.90
TIMEZONE = "Pacific/Auckland"
NIWA_URL = "https://weather-api-azure.niwa.co.nz/api/location/eastbourne/forecast" # Simplified based on NIWA docs

# User Weights
WEIGHTS = {
    'NIWA': 0.80,
    'ECMWF': 0.15,
    'GFS': 0.05
}

# Color Mapping
def get_wind_color(speed):
    if speed < 5: return "#ADD8E6"    # Light Blue
    if speed < 10: return "#0000FF"   # Blue
    if speed < 15: return "#008000"   # Green
    if speed < 20: return "#FFFF00"   # Yellow (Text should be dark)
    if speed < 28: return "#FF0000"   # Red
    return "#8B0000"                  # Dark Red

# --- DATA FETCHING ---

def fetch_open_meteo():
    """Fetches ECMWF and GFS from Open-Meteo in Knots."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "hourly": ["wind_speed_10m", "wind_direction_10m"],
        "wind_speed_unit": "kn",
        "models": ["ecmwf_ifs", "gfs_seamless"],
        "timezone": TIMEZONE,
        "forecast_days": 7
    }
    r = requests.get(url, params=params).json()
    
    df_ecmwf = pd.DataFrame({
        'time': pd.to_datetime(r['hourly']['time']),
        'ecmwf_speed': r['hourly']['wind_speed_10m_ecmwf_ifs'],
        'dir': r['hourly']['wind_direction_10m_ecmwf_ifs']
    })
    
    df_gfs = pd.DataFrame({
        'time': pd.to_datetime(r['hourly']['time']),
        'gfs_speed': r['hourly']['wind_speed_10m_gfs_seamless']
    })
    
    return pd.merge(df_ecmwf, df_gfs, on='time')

def fetch_niwa(api_key):
    """Fetches NIWA data. Pretends km/h = knots as requested."""
    headers = {"x-api-key": api_key}
    # Note: If this endpoint differs in your portal, update the URL.
    try:
        r = requests.get(NIWA_URL, headers=headers).json()
        # Parsing logic depends on NIWA's exact JSON structure
        # Assuming a common structure: list of values with valid_at
        data = []
        for item in r.get('values', []):
            data.append({
                'time': pd.to_datetime(item['valid_at']).tz_convert(TIMEZONE).tz_localize(None),
                'niwa_speed': item['wind_speed'] # Pretending km/h is knots
            })
        return pd.DataFrame(data)
    except:
        st.error("Failed to fetch NIWA data. Check API Key or Endpoint.")
        return pd.DataFrame()

# --- PROCESSING ---

def apply_logic(df):
    # 1. Weighted Average
    df['consensus'] = (
        (df['niwa_speed'] * WEIGHTS['NIWA']) +
        (df['ecmwf_speed'] * WEIGHTS['ECMWF']) +
        (df['gfs_speed'] * WEIGHTS['GFS'])
    )
    
    # 2. Eastbourne Wrap (1.15x for 300-340 degrees)
    df.loc[(df['dir'] >= 300) & (df['dir'] <= 340), 'consensus'] *= 1.15
    
    # 3. Night/Day Calculation
    sun = Sun(LAT, LON)
    tz = pytz.timezone(TIMEZONE)
    
    def is_night(row):
        date = row['time'].date()
        s_rise = sun.get_local_sunrise_time(date).replace(tzinfo=None)
        s_set = sun.get_local_sunset_time(date).replace(tzinfo=None)
        return not (s_rise <= row['time'] <= s_set)
    
    df['is_night'] = df.apply(is_night, axis=1)
    return df

# --- UI SETUP ---

st.set_page_config(page_title="Eastbourne Wind", layout="wide")

st.title("🌬️ Eastbourne Wind Consensus")
st.caption(f"Lat: {LAT}, Lon: {LON} | 80% NIWA / 15% ECMWF / 5% GFS")

# API Key Check
api_key = st.secrets.get("NIWA_API_KEY")
if not api_key:
    st.warning("Please set NIWA_API_KEY in Streamlit Secrets.")
    st.stop()

# Load Data
with st.spinner("Fetching models..."):
    om_data = fetch_open_meteo()
    niwa_data = fetch_niwa(api_key)
    
    if niwa_data.empty:
        st.stop()
        
    df = pd.merge(om_data, niwa_data, on='time', how='inner')
    df = apply_logic(df)

# Toggle for night data
show_night = st.sidebar.toggle("Show Night Time Data", value=False)

if not show_night:
    df_display = df[df['is_night'] == False]
else:
    df_display = df

# --- SUMMARY TILES ---
current_hour = df_display.iloc[0]
trend_4h = df_display.iloc[4]['consensus'] - current_hour['consensus']
trend_text = "Rising" if trend_4h > 0 else "Falling"

col1, col2 = st.columns(2)
with col1:
    st.metric("Current Consensus", f"{current_hour['consensus']:.1f} kts")
    st.markdown(f"<div style='background-color:{get_wind_color(current_hour['consensus'])}; height:10px; border-radius:5px;'></div>", unsafe_allow_html=True)

with col2:
    st.metric("4-Hour Trend", f"{trend_text}", f"{trend_4h:+.1f} kts")

# --- HORIZONTAL SCROLLABLE CARDS (Next 48 Hours) ---
st.subheader("Hourly Forecast (Next 48h)")

# Horizontal Scroll CSS
st.markdown("""
    <style>
    .scroll-container {
        display: flex;
        overflow-x: auto;
        gap: 10px;
        padding-bottom: 15px;
    }
    .wind-card {
        min-width: 80px;
        text-align: center;
        padding: 10px;
        border-radius: 10px;
        color: black;
        font-weight: bold;
    }
    .day-divider {
        min-width: 2px;
        background-color: #ccc;
        margin: 0 10px;
    }
    </style>
""", unsafe_allow_html=True)

html_content = '<div class="scroll-container">'
last_day = None

for i, row in df_display.head(48).iterrows():
    # Day Split logic
    current_day = row['time'].strftime("%a")
    if last_day and current_day != last_day:
        html_content += f'<div class="day-divider"></div><div style="writing-mode: vertical-rl; font-size: 12px; align-self: center;">{current_day}</div>'
    
    color = get_wind_color(row['consensus'])
    text_color = "black" if color == "#FFFF00" else "white"
    
    html_content += f"""
        <div class="wind-card" style="background-color: {color}; color: {text_color};">
            <div style="font-size: 10px;">{row['time'].strftime('%H:%M')}</div>
            <div style="font-size: 18px;">{row['consensus']:.0f}</div>
            <div style="font-size: 9px;">kts</div>
            <div style="font-size: 9px; transform: rotate({row['dir']}deg);">↑</div>
        </div>
    """
    last_day = current_day

html_content += '</div>'
st.markdown(html_content, unsafe_allow_html=True)

# Full Data Table (Optional)
with st.expander("View Raw Data Table"):
    st.dataframe(df)
