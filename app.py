import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import time
import random
import urllib.parse
import io
import openpyxl
import logging
import json
import hashlib
import os
from datetime import datetime
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(level=logging.INFO)

# ---------------- Config / Globals ----------------
MARKETPLACES = {
    "US": "www.amazon.com", "CA": "www.amazon.ca", "GB": "www.amazon.co.uk", 
    "DE": "www.amazon.de", "FR": "www.amazon.fr", "ES": "www.amazon.es", 
    "IN": "www.amazon.in", "MX": "www.amazon.com.mx", "AU": "www.amazon.com.au", 
    "IT": "www.amazon.it", "JP": "www.amazon.co.jp", "AE": "www.amazon.ae",
    "NL": "www.amazon.nl", "BR": "www.amazon.com.br", "SG": "www.amazon.sg", 
    "TR": "www.amazon.com.tr", "SA": "www.amazon.sa", "PL": "www.amazon.pl", 
    "SE": "www.amazon.se", "BE": "www.amazon.com.be", "EG": "www.amazon.eg"
}

AVAILABLE_COLUMNS = {
    "ASIN": "Product ASIN",
    "Brand": "Product Brand",
    "Marketplace": "Marketplace Code",
    "Keyword": "Search Keyword",
    "FetchedAt": "Timestamp",
    "Parent_ASIN": "Parent ASIN",
    "Rank": "Best Sellers Rank",
    "Title": "Product Title",
    "Customer_Rating": "Average Rating",
    "Review_Count": "Number of Reviews",
    "GL": "General Ledger Type",
    "Item_Type_Keyword": "Item Type Keyword",
    "Product_Type": "Product Type"
}


# Directory structure
CHECKPOINT_DIR = Path(".asin_checkpoints")
ADMIN_DATA_DIR = Path(".admin_data")
CHECKPOINT_DIR.mkdir(exist_ok=True)
ADMIN_DATA_DIR.mkdir(exist_ok=True)

# File paths
TEMP_RESULTS = CHECKPOINT_DIR / "temp_asin_results.csv"
STATE_FILE = CHECKPOINT_DIR / "checkpoint_state.json"
ADMIN_DATA_FILE = ADMIN_DATA_DIR / "admin_search_history.csv"
ADMIN_PASSWORD_FILE = ADMIN_DATA_DIR / "admin_password.txt"

# Default settings
DEFAULT_ADMIN_PASSWORD = "********"  # Change this for production
REQUEST_TIMEOUT = 15  # seconds
BASE_DELAY = (0.8, 1.8)
RETRY_STATUS = (429, 500, 502, 503, 504)

# ---------------- Password Management ----------------
def hash_password(password, salt=None):
    """Hash password with salt"""
    if salt is None:
        salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        100000
    )
    return salt + key

def verify_password(stored_password_hash, provided_password):
    """Verify password against stored hash"""
    salt = stored_password_hash[:32]
    stored_key = stored_password_hash[32:]
    new_key = hashlib.pbkdf2_hmac(
        'sha256',
        provided_password.encode('utf-8'),
        salt,
        100000
    )
    return stored_key == new_key

def save_hashed_password(password):
    """Save hashed password to file"""
    hashed = hash_password(password)
    ADMIN_PASSWORD_FILE.write_bytes(hashed)

def initialize_admin_password():
    """Initialize admin password if it doesn't exist"""
    if not ADMIN_PASSWORD_FILE.exists():
        save_hashed_password(DEFAULT_ADMIN_PASSWORD)

def verify_admin_password(password):
    """Verify if provided password matches stored hashed password"""
    if not ADMIN_PASSWORD_FILE.exists():
        initialize_admin_password()
    stored_hash = ADMIN_PASSWORD_FILE.read_bytes()
    return verify_password(stored_hash, password)

def change_admin_password(current_password, new_password):
    """Change admin password"""
    if verify_admin_password(current_password):
        save_hashed_password(new_password)
        return True
    return False
# ---------------- Utilities ----------------
def make_session(ua_string=None, retries=3, backoff_factor=0.6):
    session = requests.Session()
    try:
        ua = ua_string or UserAgent().random
    except Exception:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    session.headers.update({
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    })
    retry = Retry(total=retries, backoff_factor=backoff_factor, status_forcelist=RETRY_STATUS, allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def extract_product_details(product_div, selected_columns):
    """Extract selected product details from product div"""
    details = {}
    
    if "ASIN" in selected_columns:
        details["ASIN"] = product_div.get('data-asin', '')
    
    if "Brand" in selected_columns:
        details["Brand"] = extract_brand(product_div)
    
    if "Title" in selected_columns:
        title_elem = product_div.find('span', {'class': 'a-text-normal'})
        details["Title"] = title_elem.get_text(strip=True) if title_elem else ''
    
    if "Customer_Rating" in selected_columns:
        rating_elem = product_div.find('span', {'class': 'a-icon-alt'})
        if rating_elem:
            rating_text = rating_elem.get_text(strip=True)
            details["Customer_Rating"] = rating_text.split(' ')[0] if rating_text else ''
        else:
            details["Customer_Rating"] = ''
    
    if "Review_Count" in selected_columns:
        review_elem = product_div.find('span', {'class': 'a-size-base s-underline-text'})
        details["Review_Count"] = review_elem.get_text(strip=True) if review_elem else '0'
    
    if "Parent_ASIN" in selected_columns:
        details["Parent_ASIN"] = ''  # Would require additional API call or page scraping
    
    if "Rank" in selected_columns:
        details["Rank"] = ''  # Would require product page scraping
    
    if "GL" in selected_columns:
        details["GL"] = extract_gl_type(product_div)
    
    if "Item_Type_Keyword" in selected_columns:
        details["Item_Type_Keyword"] = extract_item_type_Keyword(product_div)
    
    if "Product_Type" in selected_columns:
        details["Product_Type"] = extract_product_type(product_div)
    
    return details

def extract_gl_type(product_div):
    """Extract General Ledger type from product div"""
    # This might require deeper page analysis or API call
    # For now, we'll check if we can find any text containing 'gl_'
    gl_elem = product_div.find(text=lambda t: t and t.startswith('gl_'))
    return gl_elem.strip() if gl_elem else ''

def extract_item_type_Keyword(product_div):
    """Extract Item Type from product div"""
    # This might require deeper page analysis or API call
    # For now, we'll look for common item type indicators
    item_type_Keyword_elem = product_div.find(text=lambda t: t and any(word in t.lower() for word in ['case', 'accessory', 'charger']))
    return item_type_Keyword_elem.strip() if item_type_Keyword_elem else ''

def extract_product_type(product_div):
    """Extract Product Type from product div"""
    # This might require deeper page analysis or API call
    # For now, we'll look for text that matches the format like CELLULAR_PHONE_CASE
    product_type_elem = product_div.find(text=lambda t: t and t.isupper() and '_' in t)
    return product_type_elem.strip() if product_type_elem else ''

def extract_brand(product_div):
    """Extract brand information from product div"""
    patterns = [
        ('span', {'class': 'a-size-base-plus a-color-base'}),
        ('a', {'class': 'a-size-base-plus a-color-base'}),
        ('span', {'class': 'a-size-base'}),
        ('a', {'class': 'a-link-normal'}),
    ]
    for tag, attrs in patterns:
        el = product_div.find(tag, attrs)
        if el:
            t = el.get_text(strip=True)
            if t:
                return t
    title = product_div.find('span', {'class': 'a-text-normal'})
    if title:
        t = title.get_text(strip=True)
        if t:
            return t.split()[0]
    return "Unknown Brand"

def save_admin_record(marketplace, keyword, asin_count):
    """Save search operation record for admin tracking"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    record = pd.DataFrame([{
        'Timestamp': timestamp,
        'Marketplace': marketplace,
        'Keyword': keyword,
        'ASINs_Found': asin_count
    }])
    
    if ADMIN_DATA_FILE.exists():
        record.to_csv(ADMIN_DATA_FILE, mode='a', header=False, index=False)
    else:
        record.to_csv(ADMIN_DATA_FILE, index=False)

def load_admin_data():
    """Load admin history data"""
    if ADMIN_DATA_FILE.exists():
        return pd.read_csv(ADMIN_DATA_FILE)
    return pd.DataFrame(columns=['Timestamp', 'Marketplace', 'Keyword', 'ASINs_Found'])

def append_partial_rows(rows, columns):
    """Append rows to temporary results with specified columns"""
    if not rows:
        return
    df = pd.DataFrame(rows, columns=columns)
    if TEMP_RESULTS.exists():
        df.to_csv(TEMP_RESULTS, mode="a", index=False, header=False)
    else:
        df.to_csv(TEMP_RESULTS, index=False, header=True)

def load_partial_df():
    if TEMP_RESULTS.exists():
        return pd.read_csv(TEMP_RESULTS, dtype=str)
    return pd.DataFrame(columns=list(AVAILABLE_COLUMNS.keys()))

def clear_checkpoints():
    """Clear user checkpoints but preserve admin data"""
    if TEMP_RESULTS.exists():
        TEMP_RESULTS.unlink(missing_ok=True)
    if STATE_FILE.exists():
        STATE_FILE.unlink(missing_ok=True)

def build_search_url(marketplace_host, search_term, page):
    q = urllib.parse.quote(search_term)
    return f"https://{marketplace_host}/s?k={q}&page={page}"

def save_final_files(df, search_terms):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = "_".join([t.replace(" ", "_") for t in search_terms[:2]])
    if len(search_terms) > 2:
        short += "_and_more"
    csv_name = f"amazon_asins_{short}_{ts}.csv"
    xlsx_name = f"amazon_asins_{short}_{ts}.xlsx"
    df.to_csv(csv_name, index=False)
    df.to_excel(xlsx_name, index=False)
    return csv_name, xlsx_name
# ---------------- Scraping Functions ----------------
def scrape_pages_for_pair(session, marketplace_code, keyword, min_asins, selected_columns, max_pages=20, start_page=1, already_asins=None):
    """Scrape Amazon pages for ASINs with selected columns"""
    if already_asins is None:
        already_asins = set()
    
    host = MARKETPLACES[marketplace_code]
    page = max(1, int(start_page))
    collected_this_run = []
    total_seen = set(already_asins)
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    while page <= max_pages and len(total_seen) < min_asins:
        url = build_search_url(host, keyword, page)
        
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 403:
                st.error(f"Access denied (403) for {marketplace_code}. Possible rate limiting.")
                break
        except Exception as e:
            st.warning(f"[{marketplace_code} • {keyword}] Request failed page {page}: {e}")
            break
            
        time.sleep(random.uniform(BASE_DELAY[0], BASE_DELAY[1]))
        
        if resp.status_code != 200:
            st.warning(f"[{marketplace_code} • {keyword}] Non-200 status {resp.status_code} on page {page}")
            break
            
        soup = BeautifulSoup(resp.content, 'lxml')
        main = soup.find('div', {'class': 's-main-slot'})
        
        if not main:
            st.info(f"[{marketplace_code} • {keyword}] No product grid on page {page}.")
            break
            
        product_divs = main.find_all('div', {'data-asin': True})
        new_rows = []
        page_added = 0
        
        for div in product_divs:
            asin = (div.get('data-asin') or "").strip()
            if not asin or asin in total_seen:
                continue
                
            details = extract_product_details(div, selected_columns)
            details.update({
                "Marketplace": marketplace_code,
                "Keyword": keyword,
                "FetchedAt": now_str
            })
            
            total_seen.add(asin)
            new_rows.append(details)
            page_added += 1
            
            if len(total_seen) >= min_asins:
                break
                
        if new_rows:
            append_partial_rows(new_rows, selected_columns)
            collected_this_run.extend(new_rows)
            
        # Update state
        state = load_state()
        pair_key = f"{marketplace_code}||{keyword}"
        state.setdefault("pairs", {})
        state["pairs"].setdefault(pair_key, {})
        state["pairs"][pair_key]["last_page"] = page
        state["pairs"][pair_key]["count"] = len(total_seen)
        state["pairs"][pair_key]["done"] = len(total_seen) >= min_asins
        save_state(state)
        
        # Progress update
        st.write(f"[{marketplace_code} • {keyword}] Page {page} scanned, +{page_added}. Total: {len(total_seen)}")
        
        if page_added == 0:
            break
            
        page += 1
    
    # Save admin record
    save_admin_record(marketplace_code, keyword, len(total_seen))
    
    return len(total_seen), page - 1

def process_search_batch(session, pairs, min_asins, max_pages, state, selected_columns):
    """Process a batch of search pairs with selected columns"""
    results = []
    for marketplace, keyword in pairs:
        pair_key = f"{marketplace}||{keyword}"
        meta = state.get("pairs", {}).get(pair_key, {})
        last_page = int(meta.get("last_page", 0))
        
        partial_df = load_partial_df()
        already_asins = set()
        if not partial_df.empty:
            mask = (partial_df["Marketplace"] == marketplace) & (partial_df["Keyword"] == keyword)
            already_asins = set(partial_df.loc[mask, "ASIN"].astype(str).tolist())
            
        if len(already_asins) >= min_asins:
            st.success(f"Already done: {marketplace} • {keyword} ({len(already_asins)} ASINs)")
            continue
            
        start_page = last_page + 1 if last_page >= 1 else 1
        total_count, last_scanned = scrape_pages_for_pair(
            session=session,
            marketplace_code=marketplace,
            keyword=keyword,
            min_asins=min_asins,
            max_pages=max_pages,
            start_page=start_page,
            already_asins=already_asins,
            selected_columns=selected_columns
        )
        
        results.append({
            "marketplace": marketplace,
            "keyword": keyword,
            "count": total_count,
            "pages": last_scanned,
            "done": total_count >= min_asins
        })
        
    return results

def filter_results(final_df, brand_filter, exclude_brands, exclude_asin_df):
    """Filter results based on brand and exclusion criteria"""
    df = final_df.copy()
    
    if exclude_brands:
        df = df[~df["Brand"].isin(exclude_brands)]
    
    if brand_filter:
        df = df[df["Brand"] == brand_filter]
    
    if exclude_asin_df is not None and not exclude_asin_df.empty:
        if "ASIN" in exclude_asin_df.columns and "Marketplace" in exclude_asin_df.columns:
            merged = pd.merge(
                df,
                exclude_asin_df[['ASIN', 'Marketplace']],
                on=['ASIN', 'Marketplace'],
                how='left',
                indicator=True
            )
            df = merged[merged['_merge'] == 'left_only'].drop('_merge', axis=1)
    
    return df
# ---------------- Admin Dashboard ----------------
def admin_dashboard():
    """Admin dashboard with password protection and management"""
    st.header("Admin Dashboard")
    
    # Initialize session state for admin authentication
    if 'admin_authenticated' not in st.session_state:
        st.session_state.admin_authenticated = False
    
    # Login screen
    if not st.session_state.admin_authenticated:
        with st.form("admin_login"):
            st.subheader("Admin Login")
            password = st.text_input("Enter admin password:", type="password")
            submitted = st.form_submit_button("Login")
            
            if submitted:
                if verify_admin_password(password):
                    st.session_state.admin_authenticated = True
                    st.success("Login successful!")
                    st.rerun()
                else:
                    st.error("Invalid password!")
        return
    
    # Logout button in sidebar
    if st.sidebar.button("Logout"):
        st.session_state.admin_authenticated = False
        st.rerun()
    
    # Password management section
    with st.expander("Password Management"):
        st.subheader("Change Admin Password")
        col1, col2 = st.columns(2)
        
        with col1:
            current_password = st.text_input("Current Password:", type="password", key="current_pwd")
            new_password = st.text_input("New Password:", type="password", key="new_pwd")
            confirm_password = st.text_input("Confirm New Password:", type="password", key="confirm_pwd")
            
            if st.button("Change Password"):
                if not current_password or not new_password or not confirm_password:
                    st.error("All fields are required!")
                elif new_password != confirm_password:
                    st.error("New passwords don't match!")
                elif len(new_password) < 8:
                    st.error("New password must be at least 8 characters long!")
                elif change_admin_password(current_password, new_password):
                    st.success("Password changed successfully!")
                    st.session_state.admin_authenticated = False
                    st.rerun()
                else:
                    st.error("Current password is incorrect!")
    
    # Data Analysis Section
    st.subheader("Search Analytics")
    
    # Load and prepare data
    admin_df = load_admin_data()
    if admin_df.empty:
        st.info("No search history data available.")
        return
    
    admin_df['Timestamp'] = pd.to_datetime(admin_df['Timestamp'])
    
    # Date range filter
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input(
            "Start Date",
            value=admin_df['Timestamp'].min().date(),
            min_value=admin_df['Timestamp'].min().date(),
            max_value=admin_df['Timestamp'].max().date()
        )
    with col2:
        end_date = st.date_input(
            "End Date",
            value=admin_df['Timestamp'].max().date(),
            min_value=admin_df['Timestamp'].min().date(),
            max_value=admin_df['Timestamp'].max().date()
        )
    
    # Apply date filter
    mask = (admin_df['Timestamp'].dt.date >= start_date) & (admin_df['Timestamp'].dt.date <= end_date)
    filtered_df = admin_df[mask]
    
    # Summary metrics
    st.subheader("Summary Metrics")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Searches", len(filtered_df))
    with col2:
        st.metric("Total ASINs Found", filtered_df['ASINs_Found'].sum())
    with col3:
        st.metric("Unique Keywords", filtered_df['Keyword'].nunique())
    with col4:
        st.metric("Avg ASINs per Search", round(filtered_df['ASINs_Found'].mean(), 1))
    
    # Daily summary
    st.subheader("Daily Search Summary")
    daily_summary = filtered_df.groupby(filtered_df['Timestamp'].dt.date).agg({
        'ASINs_Found': ['count', 'sum', 'mean']
    }).reset_index()
    
    st.dataframe(daily_summary, use_container_width=True)
    
    # Download options
    if st.button("Download Summary CSV"):
        csv = daily_summary.to_csv(index=False)
        st.download_button(
            "Click to Download",
            csv,
            "admin_summary.csv",
            "text/csv"
        )

# ---------------- Main Application ----------------
def main():
    st.set_page_config(page_title="Amazon ASIN Collector", layout="wide")
    st.title("🛒ASIN AID🏷️")
    
    # Initialize admin password if not exists
    initialize_admin_password()
    
    # Mode selection
    mode = st.sidebar.selectbox("Mode:", ["User", "Admin"])
    
    if mode == "Admin":
        admin_dashboard()
        return
    
    st.markdown("Select marketplaces and enter search terms (comma-separated). Use Start / Resume or Start Fresh.")
    
    # Column selection
    st.subheader("Data Collection Options")
    select_all_columns = st.checkbox("Select All Columns", value=False)
    
    if select_all_columns:
        selected_columns = list(AVAILABLE_COLUMNS.keys())
    else:
        selected_columns = st.multiselect(
            "Select columns to include in the report:",
            options=list(AVAILABLE_COLUMNS.keys()),
            default=["ASIN", "Brand", "Marketplace", "Keyword", "FetchedAt"],
            format_func=lambda x: AVAILABLE_COLUMNS[x]
        )
    
    if not selected_columns:
        st.warning("Please select at least one column for the report.")
        st.stop()
    
    # File upload for ASIN exclusion
    uploaded_exclude_asin_file = st.file_uploader(
        "Upload Excel to Exclude ASINs (by ASIN and Marketplace)",
        type=['xlsx', 'xls']
    )
    
    exclude_asin_df = None
    if uploaded_exclude_asin_file:
        try:
            exclude_asin_df = pd.read_excel(uploaded_exclude_asin_file)
            exclude_asin_df = exclude_asin_df[['ASIN', 'Marketplace']].dropna()
            st.success(f"Loaded ASIN+marketplace exclude list ({len(exclude_asin_df)} rows).")
        except Exception as e:
            st.error(f"Could not read the uploaded file: {e}")
    
    # Check for existing checkpoints
    checkpoint_exists = TEMP_RESULTS.exists() or STATE_FILE.exists()
    if checkpoint_exists:
        st.info("Found saved progress — you can Continue or Start Fresh (clears progress).")
    
    # Control buttons
    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        continue_btn = st.button("▶️Continue from saved progress")
    with c2:
        start_fresh_btn = st.button("🔃Start Fresh (clear saved progress)")
    with c3:
        clear_btn = st.button("🗑️Clear saved progress")
    # Continue main() function...
    
    if clear_btn:
        clear_checkpoints()
        st.success("Saved progress cleared.")
        st.rerun()
    
    if start_fresh_btn:
        clear_checkpoints()
        st.success("Starting fresh — previously saved progress removed.")
    
    # Marketplace selection
    select_all = st.checkbox("Select All Marketplaces", value=False)
    if select_all:
        selected_marketplaces = list(MARKETPLACES.keys())
    else:
        selected_marketplaces = st.multiselect(
            "Select Marketplaces:", 
            options=list(MARKETPLACES.keys()), 
            default=["US"]
        )
    
    if not selected_marketplaces:
        st.warning("Please select at least one marketplace.")
        st.stop()
    
    # Search terms input
    search_input = st.text_area(
        "Enter search terms (comma-separated):", 
        placeholder="nike shoes, adidas sneakers"
    )
    search_terms = [s.strip() for s in search_input.split(",") if s.strip()]
    
    # Configuration options
    colA, colB = st.columns(2)
    with colA:
        min_asins = st.number_input(
            "Minimum ASINs per (marketplace, keyword):", 
            min_value=1, 
            value=50
        )
    with colB:
        max_pages = st.number_input(
            "Max pages per pair (cap):", 
            min_value=1, 
            value=20
        )
    
    # Brand filtering options
    filter_type = st.selectbox(
        "Filter Options:", 
        ["No brand filter", "Search specific brand", "Exclude Licensees", "Exclude Licensees (Excel upload)"]
    )
    
    brand_filter = None
    exclude_brands = None
    
    if filter_type == "Search specific brand":
        brand_filter = st.text_input("Brand to include (exact match):")
    elif filter_type == "Exclude Licensees":
        exclude_input = st.text_input("Brands to exclude (comma-separated):")
        exclude_brands = [b.strip() for b in exclude_input.split(",") if b.strip()]
    elif filter_type == "Exclude Licensees (Excel upload)":
        uploaded = st.file_uploader(
            "Upload Excel with brands to exclude", 
            type=['xlsx','xls'], 
            key="brandlicenseexcludeupload"
        )
        if uploaded:
            try:
                df_ex = pd.read_excel(uploaded)
                exclude_brands = df_ex.iloc[:,0].dropna().astype(str).tolist()
                st.success(f"Loaded {len(exclude_brands)} brands to exclude.")
            except Exception as e:
                st.error(f"Failed to read uploaded file: {e}")
    
    # Start/Resume buttons
    start_col, resume_col = st.columns(2)
    with start_col:
        start_btn = st.button("▶️Start Collection (fresh run)")
    with resume_col:
        resume_btn = st.button("🔃Resume Collection (use saved progress)")
    
    if continue_btn:
        resume_btn = True
    
    # Generate pairs of (marketplace, keyword)
    pairs = []
    if search_terms:
        for m in selected_marketplaces:
            for kw in search_terms:
                pairs.append((m, kw))
    
    # Load existing state and add missing pairs
    state = load_state()
    state.setdefault("pairs", {})
    for pair_key in state.get("pairs", {}):
        try:
            mkt, kw = pair_key.split("||", 1)
        except Exception:
            continue
        if (mkt, kw) not in pairs:
            pairs.append((mkt, kw))
    
    # Main processing
    if start_btn or resume_btn:
        if start_btn:
            clear_checkpoints()
            state = {"pairs": {}}
            save_state(state)
        else:  # resume_btn
            state = load_state()

        # Initialize session
        try:
            ua = UserAgent().random
        except Exception:
            ua = None
        session = make_session(ua_string=ua, retries=3, backoff_factor=0.6)

        # Generate or load pairs
        pairs = []
        if search_terms:
            for m in selected_marketplaces:
                for kw in search_terms:
                    pairs.append((m, kw))
        
        if resume_btn:
            for pair_key in state.get("pairs", {}):
                try:
                    mkt, kw = pair_key.split("||", 1)
                    if (mkt, kw) not in pairs:
                        pairs.append((mkt, kw))
                except Exception:
                    continue

        if not pairs:
            st.error("No (marketplace, keyword) pairs to process. Enter search terms and select marketplaces.")
            st.stop()

        # Process all pairs
        results = process_search_batch(
            session=session,
            pairs=pairs,
            min_asins=min_asins,
            max_pages=max_pages,
            state=state,
            selected_columns=selected_columns
        )

        # Load and filter final results
        final_df = load_partial_df()
        final_df = filter_results(final_df, brand_filter, exclude_brands, exclude_asin_df)

        if not final_df.empty:
            st.session_state.results_df = final_df
            st.success("Loaded collected ASINs (you can download results).")
        else:
            st.warning("No ASINs were collected yet. Try different keywords / marketplaces or check network.")

    
    # Display results
    final_df = st.session_state.results_df if 'results_df' in st.session_state else None
    if final_df is not None and not final_df.empty:
        st.subheader("Collected ASINs (partial or final, post filtering)")
        st.dataframe(final_df, use_container_width=True)
        
        # Download options
        cold1, cold2 = st.columns(2)
        with cold1:
            csv_bytes = final_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                "Download CSV (current)", 
                data=csv_bytes, 
                file_name="asin_partial_results.csv", 
                mime="text/csv"
            )
        with cold2:
            out = io.BytesIO()
            with pd.ExcelWriter(out, engine='openpyxl') as writer:
                final_df.to_excel(writer, index=False, sheet_name="ASINs")
            st.download_button(
                "Download Excel (current)", 
                data=out.getvalue(), 
                file_name="asin_partial_results.xlsx", 
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
    
    # Final actions
    if st.button("Finalize & export full results (keeps resume files)"):
        if 'results_df' in st.session_state and st.session_state.results_df is not None:
            csv_name, xlsx_name = save_final_files(st.session_state.results_df, search_terms or ["resume"])
            st.success(f"Saved final files: {csv_name}, {xlsx_name}")
        else:
            st.warning("No results to export yet.")
    
    if st.button("🧹Clear resume files (delete checkpoints)"):
        clear_checkpoints()
        st.success("Checkpoints cleared. You can start fresh next time.")
        st.rerun()
    
    # Footer
    st.markdown("---")
    st.caption("Notes: this scrapes public Amazon search pages. Respect robots.txt and site TOS for production use; consider API if available. Use conservative delays and session retries to reduce rate-limits.")

if __name__ == "__main__":
    main()
