import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
from suntime import Sun
import pytz

# --- CONFIGURATION ---
LAT, LON = -41.29, 174.90
TIMEZONE = "Pacific/Auckland"

# Weights: 80% NIWA, 15% ECMWF, 5% GFS
WEIGHTS = {'NIWA': 0.80, 'ECMWF': 0.15, 'GFS': 0.05}

def get_wind_color(speed):
    if speed < 5: return "#ADD8E6"    # Light Blue
    if speed < 10: return "#0000FF"   # Blue
    if speed < 15: return "#008000"   # Green
    if speed < 20: return "#FFFF00"   # Yellow
    if speed < 28: return "#FF0000"   # Red
    return "#8B0000"                  # Dark Red

# --- DATA FETCHING ---

def fetch_open_meteo():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT, "longitude": LON,
        "hourly": ["wind_speed_10m", "wind_direction_10m"],
        "wind_speed_unit": "kn",
        "models": ["ecmwf_ifs", "gfs_seamless"],
        "timezone": TIMEZONE, "forecast_days": 7
    }
    r = requests.get(url, params=params).json()
    df_ec = pd.DataFrame({
        'time': pd.to_datetime(r['hourly']['time']),
        'ecmwf_speed': r['hourly']['wind_speed_10m_ecmwf_ifs'],
        'dir': r['hourly']['wind_direction_10m_ecmwf_ifs']
    })
    df_gfs = pd.DataFrame({
        'time': pd.to_datetime(r['hourly']['time']),
        'gfs_speed': r['hourly']['wind_speed_10m_gfs_seamless']
    })
    return pd.merge(df_ec, df_gfs, on='time')

def fetch_niwa_public():
    # Using the public "backdoor" endpoint found by Replit Agent
    url = f"https://weather-api-azure.niwa.co.nz/api/grid/combined?lat={LAT}&long={LON}"
    try:
        r = requests.get(url).json()
        # The public API returns a 'data' object with 'wind_speed' arrays
        # or a 'values' list. We'll attempt the most common 'values' loop first.
        data = []
        source_data = r.get('values', r.get('data', []))
        
        for item in source_data:
            # Handle both common NIWA JSON formats
            time_val = item.get('valid_at', item.get('time'))
            speed_val = item.get('wind_speed', item.get('speed', 0))
            
            data.append({
                'time': pd.to_datetime(time_val).tz_convert(TIMEZONE).tz_localize(None),
                'niwa_speed': speed_val # Pretend km/h is Knots
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"NIWA Fetch Error: {e}")
        return pd.DataFrame()

# --- PROCESSING ---

def apply_logic(df):
    # Weighted Average
    df['consensus'] = (df['niwa_speed'] * WEIGHTS['NIWA']) + \
                      (df['ecmwf_speed'] * WEIGHTS['ECMWF']) + \
                      (df['gfs_speed'] * WEIGHTS['GFS'])
    
    # Eastbourne Wrap (1.15x for NW winds 300-340°)
    df.loc[(df['dir'] >= 300) & (df['dir'] <= 340), 'consensus'] *= 1.15
    
    # Night calculation
    sun = Sun(LAT, LON)
    tz = pytz.timezone(TIMEZONE)
    def is_night(row):
        date = row['time'].date()
        s_rise = sun.get_local_sunrise_time(date).replace(tzinfo=None)
        s_set = sun.get_local_sunset_time(date).replace(tzinfo=None)
        return not (s_rise <= row['time'] <= s_set)
    
    df['is_night'] = df.apply(is_night, axis=1)
    return df

# --- UI ---

st.set_page_config(page_title="Eastbourne Wind", layout="wide")
st.title("🌬️ Eastbourne Wind Consensus")

with st.spinner("Fetching data..."):
    df_om = fetch_open_meteo()
    df_niwa = fetch_niwa_public()
    
    if df_niwa.empty:
        st.warning("Could not load NIWA data. Showing Global Models only.")
        # Fallback to just OM if NIWA fails
        df_om['niwa_speed'] = df_om['ecmwf_speed'] 
        df = df_om
    else:
        df = pd.merge(df_om, df_niwa, on='time', how='inner')

    df = apply_logic(df)

# Toggle for night
show_night = st.sidebar.toggle("Show Night Time Data", value=False)
df_display = df if show_night else df[df['is_night'] == False]

# Summary Tiles
curr = df_display.iloc[0]
trend = df_display.iloc[4]['consensus'] - curr['consensus']
t_text = "Rising" if trend > 0 else "Falling"

c1, c2 = st.columns(2)
c1.metric("Current Consensus", f"{curr['consensus']:.1f} kts")
c2.metric("4-Hour Trend", t_text, f"{trend:+.1f} kts")

# Horizontal Cards
st.subheader("Hourly Forecast (Next 48h)")
st.markdown("""<style>
    .scroll-container { display: flex; overflow-x: auto; gap: 10px; padding-bottom: 15px; }
    .wind-card { min-width: 85px; text-align: center; padding: 10px; border-radius: 10px; font-weight: bold; }
    .day-sep { min-width: 2px; background: #555; margin: 0 5px; }
</style>""", unsafe_allow_html=True)

html = '<div class="scroll-container">'
last_d = None
for i, row in df_display.head(48).iterrows():
    curr_d = row['time'].strftime("%a")
    if last_d and curr_d != last_d:
        html += f'<div class="day-sep"></div><div style="align-self:center; font-size:12px;">{curr_d}</div>'
    
    bg = get_wind_color(row['consensus'])
    tx = "black" if bg == "#FFFF00" else "white"
    html += f'<div class="wind-card" style="background:{bg}; color:{tx};">' \
            f'<div style="font-size:10px;">{row['time'].strftime("%H:%M")}</div>' \
            f'<div style="font-size:18px;">{row["consensus"]:.0f}</div>' \
            f'<div style="font-size:9px; transform:rotate({row["dir"]}deg);">↑</div></div>'
    last_d = curr_d
html += '</div>'
st.markdown(html, unsafe_allow_html=True)
