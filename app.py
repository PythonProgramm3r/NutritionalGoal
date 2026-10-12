import os
import sqlite3
import smtplib
from email.message import EmailMessage
from datetime import date, timedelta
from io import BytesIO
import streamlit as st
import polars as pl
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from init_db import DB_PATH, seed_staples_for_user


class NutritionExtraction(BaseModel):
    item_name: str = Field(description="Inferred food name or 'Scanned Label'")
    serving_size: str = Field(description="Serving size text from the label")
    calories: int = Field(description="Kilocalories per single serving")
    protein_g: float = Field(description="Grams of protein per single serving")
    carbs_g: float = Field(description="Grams of total carbohydrates per single serving")
    fat_g: float = Field(description="Grams of total fat per single serving")


# --- DATABASE QUERIES ---
def get_all_users() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, username, pin, sms_gateway, kcal, protein_g, carbs_g, fat_g FROM users ORDER BY id"
        ).fetchall()
    return [
        {
            "id": r[0],
            "username": r[1],
            "pin": r[2] or "",
            "sms_gateway": r[3] or "",
            "kcal": r[4],
            "protein_g": r[5],
            "carbs_g": r[6],
            "fat_g": r[7],
        }
        for r in rows
    ]


def create_user(
    username: str,
    pin: str,
    sms_gateway: str,
    kcal: int,
    pro: float,
    carb: float,
    fat: float,
    seed_defaults: bool = True,
):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO users (username, pin, sms_gateway, kcal, protein_g, carbs_g, fat_g)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (username.strip(), pin.strip(), sms_gateway.strip(), kcal, pro, carb, fat),
        )
        if seed_defaults:
            seed_staples_for_user(conn, cur.lastrowid)


def update_user_settings(
    user_id: int,
    kcal: int,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
    sms_gateway: str,
    pin: str,
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """UPDATE users
               SET kcal = ?, protein_g = ?, carbs_g = ?, fat_g = ?, sms_gateway = ?, pin = ?
               WHERE id = ?""",
            (kcal, protein_g, carbs_g, fat_g, sms_gateway.strip(), pin.strip(), user_id),
        )


def get_staples(user_id: int) -> list[tuple]:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT id, item_name, serving_size, kcal, protein_g, carbs_g, fat_g FROM staples WHERE user_id = ? ORDER BY item_name",
            (user_id,),
        ).fetchall()


def save_staple(
    user_id: int,
    item_name: str,
    serving_size: str,
    kcal: int,
    protein_g: float,
    carbs_g: float,
    fat_g: float,
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO staples (user_id, item_name, serving_size, kcal, protein_g, carbs_g, fat_g)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, item_name) DO UPDATE SET
                   serving_size = excluded.serving_size,
                   kcal = excluded.kcal,
                   protein_g = excluded.protein_g,
                   carbs_g = excluded.carbs_g,
                   fat_g = excluded.fat_g""",
            (user_id, item_name.strip(), serving_size.strip(), kcal, protein_g, carbs_g, fat_g),
        )


def delete_staple(staple_id: int, user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM staples WHERE id = ? AND user_id = ?", (staple_id, user_id))


def log_food_entry(
    user_id: int,
    log_date: str,
    item_name: str,
    servings: float,
    base_kcal: int,
    base_pro: float,
    base_carb: float,
    base_fat: float,
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO logs (user_id, log_date, item_name, servings, kcal, protein_g, carbs_g, fat_g)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                log_date,
                item_name,
                servings,
                int(round(base_kcal * servings)),
                round(base_pro * servings, 1),
                round(base_carb * servings, 1),
                round(base_fat * servings, 1),
            ),
        )


def get_7day_summary(user: dict, today: date) -> pl.DataFrame:
    """Builds a contiguous 7-day Polars DataFrame with macro totals and target adherence."""
    start_date = (today - timedelta(days=6)).isoformat()
    end_date = today.isoformat()

    date_spine = pl.DataFrame(
        {"Date": [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]}
    )

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT log_date, kcal, protein_g, carbs_g, fat_g
               FROM logs
               WHERE user_id = ? AND log_date BETWEEN ? AND ?""",
            (user["id"], start_date, end_date),
        ).fetchall()

    if rows:
        raw_df = pl.DataFrame(
            rows,
            schema=["Date", "Kcal", "Protein (g)", "Carbs (g)", "Fat (g)"],
            orient="row",
        )
        agg_df = raw_df.group_by("Date").agg(
            [
                pl.col("Kcal").sum(),
                pl.col("Protein (g)").sum().round(1),
                pl.col("Carbs (g)").sum().round(1),
                pl.col("Fat (g)").sum().round(1),
            ]
        )
        df_7d = date_spine.join(agg_df, on="Date", how="left").fill_null(0)
    else:
        df_7d = date_spine.with_columns(
            [
                pl.lit(0).cast(pl.Int64).alias("Kcal"),
                pl.lit(0.0).alias("Protein (g)"),
                pl.lit(0.0).alias("Carbs (g)"),
                pl.lit(0.0).alias("Fat (g)"),
            ]
        )

    kcal_target = max(user["kcal"], 1)
    pro_target = max(user["protein_g"], 1.0)

    return df_7d.with_columns(
        [
            pl.lit(user["kcal"]).alias("Target Kcal"),
            pl.lit(user["protein_g"]).alias("Target Protein (g)"),
            ((pl.col("Kcal") / kcal_target) * 100).round(1).alias("Kcal %"),
            ((pl.col("Protein (g)") / pro_target) * 100).round(1).alias("Protein %"),
            (
                (pl.col("Kcal") >= user["kcal"])
                & (pl.col("Protein (g)") >= user["protein_g"])
            ).alias("Goal Hit"),
        ]
    )


# --- SMS & VISION HELPERS ---
def send_goal_sms(user: dict, totals: dict) -> bool:
    """Sends a free SMS via Gmail SMTP to the active user's carrier Email-to-SMS gateway."""
    smtp_email = os.getenv("SMTP_EMAIL")
    smtp_password = os.getenv("SMTP_APP_PASSWORD", "").replace(" ", "")
    sms_gateway = user["sms_gateway"] or os.getenv("SMS_GATEWAY_ADDRESS")

    if not all([smtp_email, smtp_password, sms_gateway]):
        st.warning(
            f"Goal reached for {user['username']}! (Set an SMS gateway address to receive text alerts)."
        )
        return False

    body = (
        f"{user['username']} Goal Hit! "
        f"Kcal:{totals['kcal']}/{user['kcal']} | "
        f"P:{totals['protein_g']:.0f}g | "
        f"C:{totals['carbs_g']:.0f}g | "
        f"F:{totals['fat_g']:.0f}g"
    )

    msg = EmailMessage()
    msg.set_content(body)
    msg["From"] = smtp_email
    msg["To"] = sms_gateway

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(smtp_email, smtp_password)
            server.send_message(msg)
        return True
    except Exception as e:
        st.error(f"SMS Gateway Error: {e}")
        return False


def check_and_trigger_sms(user: dict, today_str: str, totals: dict):
    """Triggers SMS once per day per user when kcal and protein targets are reached."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT sent FROM sms_status WHERE user_id = ? AND log_date = ?",
            (user["id"], today_str),
        ).fetchone()
        already_sent = row[0] == 1 if row else False

        kcal_hit = totals["kcal"] >= user["kcal"]
        protein_hit = totals["protein_g"] >= user["protein_g"]

        if (kcal_hit and protein_hit) and not already_sent:
            if send_goal_sms(user, totals):
                conn.execute(
                    "INSERT OR REPLACE INTO sms_status (user_id, log_date, sent) VALUES (?, ?, 1)",
                    (user["id"], today_str),
                )
                st.success(f"Daily goal reached! SMS sent to {user['username']}.")


def parse_nutrition_label(image_bytes: bytes) -> NutritionExtraction:
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    img = Image.open(BytesIO(image_bytes))
    prompt = (
        "Extract the nutritional information per single serving from this nutrition facts label. "
        "Be exact with kilocalories, protein, total carbohydrates, and total fat in grams."
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[img, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=NutritionExtraction,
            temperature=0.1,
        ),
    )
    return NutritionExtraction.model_validate_json(response.text)


# --- UI LAYOUT ---
st.set_page_config(page_title="Macro & Kcal Tracker", layout="centered")
today_obj = date.today()
today_str = today_obj.isoformat()
users = get_all_users()
user_map = {u["username"]: u for u in users}

# Sidebar: User Switcher, Target Settings & Add User
with st.sidebar:
    st.header("Profile")
    selected_username = st.selectbox("Active User", options=list(user_map.keys()))
    active_user = user_map[selected_username]

    pin_unlocked = True
    if active_user["pin"]:
        entered_pin = st.text_input(
            f"Enter PIN for {active_user['username']}", type="password"
        )
        if entered_pin != active_user["pin"]:
            pin_unlocked = False
            st.warning("Enter your PIN in the sidebar to view and log entries.")

    if pin_unlocked:
        st.subheader(f"{active_user['username']}'s Targets & SMS")
        with st.form("goals_form"):
            new_kcal = st.number_input(
                "Daily Kilocalories (kcal)", value=active_user["kcal"], step=50
            )
            new_pro = st.number_input(
                "Protein (g)", value=active_user["protein_g"], step=5.0
            )
            new_carb = st.number_input(
                "Carbs (g)", value=active_user["carbs_g"], step=5.0
            )
            new_fat = st.number_input(
                "Fat (g)", value=active_user["fat_g"], step=5.0
            )
            new_gw = st.text_input(
                "SMS Gateway (e.g. 10digit@vtext.com)", value=active_user["sms_gateway"]
            )
            new_pin = st.text_input(
                "Optional Profile PIN", value=active_user["pin"], type="password"
            )
            if st.form_submit_button("Save Profile Settings"):
                update_user_settings(
                    active_user["id"],
                    new_kcal,
                    new_pro,
                    new_carb,
                    new_fat,
                    new_gw,
                    new_pin,
                )
                st.toast("Profile updated!")
                st.rerun()

    st.divider()
    with st.expander("➕ Add New User to DB"):
        with st.form("new_user_form"):
            nu_name = st.text_input("Username")
            nu_gw = st.text_input(
                "SMS Gateway Address", placeholder="5551234567@vtext.com"
            )
            nu_pin = st.text_input("Optional PIN", type="password")
            nu_kcal = st.number_input("Target Kcal", value=2000, step=50)
            nu_pro = st.number_input("Target Protein (g)", value=150.0, step=5.0)
            nu_carb = st.number_input("Target Carbs (g)", value=180.0, step=5.0)
            nu_fat = st.number_input("Target Fat (g)", value=65.0, step=5.0)
            nu_seed = st.checkbox("Pre-load default staple foods", value=True)
            if st.form_submit_button("Create User"):
                if nu_name.strip():
                    try:
                        create_user(
                            nu_name,
                            nu_pin,
                            nu_gw,
                            nu_kcal,
                            nu_pro,
                            nu_carb,
                            nu_fat,
                            nu_seed,
                        )
                        st.success(f"Added {nu_name}!")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Username already exists.")

if not pin_unlocked:
    st.stop()

st.title(f"{active_user['username']}'s Nutrition Tracker")

# Fetch Today's Logs for Active User into Polars
with sqlite3.connect(DB_PATH) as conn:
    rows = conn.execute(
        "SELECT id, item_name, servings, kcal, protein_g, carbs_g, fat_g FROM logs WHERE user_id = ? AND log_date = ?",
        (active_user["id"], today_str),
    ).fetchall()

df = (
    pl.DataFrame(
        rows,
        schema=[
            "id",
            "Food Item",
            "Servings",
            "Kcal",
            "Protein (g)",
            "Carbs (g)",
            "Fat (g)",
        ],
        orient="row",
    )
    if rows
    else pl.DataFrame(
        schema={
            "id": pl.Int64,
            "Food Item": pl.Utf8,
            "Servings": pl.Float64,
            "Kcal": pl.Int64,
            "Protein (g)": pl.Float64,
            "Carbs (g)": pl.Float64,
            "Fat (g)": pl.Float64,
        }
    )
)

totals = {
    "kcal": int(df["Kcal"].sum()) if not df.is_empty() else 0,
    "protein_g": float(df["Protein (g)"].sum()) if not df.is_empty() else 0.0,
    "carbs_g": float(df["Carbs (g)"].sum()) if not df.is_empty() else 0.0,
    "fat_g": float(df["Fat (g)"].sum()) if not df.is_empty() else 0.0,
}

# Check SMS trigger for Active User
check_and_trigger_sms(active_user, today_str, totals)

# Top-Level View Tabs
tab_today, tab_history = st.tabs(["Today's Tracker", "7-Day Polars Trends"])

# ==========================================
# TAB 1: TODAY'S TRACKER
# ==========================================
with tab_today:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Kilocalories", f"{totals['kcal']} / {active_user['kcal']}")
    col1.progress(min(totals["kcal"] / max(active_user["kcal"], 1), 1.0))

    col2.metric(
        "Protein", f"{totals['protein_g']:.0f}/{active_user['protein_g']:.0f}g"
    )
    col2.progress(min(totals["protein_g"] / max(active_user["protein_g"], 1.0), 1.0))

    col3.metric("Carbs", f"{totals['carbs_g']:.0f}/{active_user['carbs_g']:.0f}g")
    col3.progress(min(totals["carbs_g"] / max(active_user["carbs_g"], 1.0), 1.0))

    col4.metric("Fat", f"{totals['fat_g']:.0f}/{active_user['fat_g']:.0f}g")
    col4.progress(min(totals["fat_g"] / max(active_user["fat_g"], 1.0), 1.0))

    st.divider()

    # --- QUICK-ADD STAPLES ---
    hdr_col, mult_col = st.columns([2, 1])
    hdr_col.subheader("Quick-Add Staples")
    quick_servings = mult_col.number_input(
        "Servings per tap", value=1.0, min_value=0.25, step=0.25
    )

    staples = get_staples(active_user["id"])
    if staples:
        grid_cols = st.columns(2)
        for idx, (s_id, s_name, s_size, s_kcal, s_pro, s_carb, s_fat) in enumerate(
            staples
        ):
            btn_label = f"+ {s_name} ({s_size})\n{int(s_kcal * quick_servings)} kcal | {s_pro * quick_servings:.0f}g P"
            if grid_cols[idx % 2].button(
                btn_label, key=f"staple_{s_id}", use_container_width=True
            ):
                log_food_entry(
                    active_user["id"],
                    today_str,
                    s_name,
                    quick_servings,
                    s_kcal,
                    s_pro,
                    s_carb,
                    s_fat,
                )
                st.toast(f"Logged {quick_servings:g}x {s_name}!")
                st.rerun()

    with st.expander("Manage or Manually Add Staples"):
        with st.form("manual_staple_form"):
            st.caption(f"Add or update a staple for {active_user['username']}")
            ms_col1, ms_col2 = st.columns(2)
            m_name = ms_col1.text_input("Food Name")
            m_size = ms_col2.text_input("Serving Size", value="1 serving")
            mc1, mc2, mc3, mc4 = st.columns(4)
            m_kcal = mc1.number_input("Kcal", value=150, step=10)
            m_pro = mc2.number_input("Protein (g)", value=15.0, step=1.0)
            m_carb = mc3.number_input("Carbs (g)", value=5.0, step=1.0)
            m_fat = mc4.number_input("Fat (g)", value=5.0, step=1.0)
            if st.form_submit_button("Save Staple") and m_name.strip():
                save_staple(
                    active_user["id"], m_name, m_size, m_kcal, m_pro, m_carb, m_fat
                )
                st.rerun()

        if staples:
            st.caption("Remove an existing staple")
            del_col1, del_col2 = st.columns([3, 1])
            staple_map = {f"{s[1]} ({s[2]})": s[0] for s in staples}
            selected_del = del_col1.selectbox(
                "Select staple",
                options=list(staple_map.keys()),
                label_visibility="collapsed",
            )
            if del_col2.button("Delete", use_container_width=True):
                delete_staple(staple_map[selected_del], active_user["id"])
                st.rerun()

    st.divider()

    # --- LABEL SCANNER ---
    st.subheader("Scan Nutrition Label")
    tab_cam, tab_upload = st.tabs(["Camera", "Upload Photo"])

    img_file = None
    with tab_cam:
        cam_img = st.camera_input("Take a picture of the nutrition label")
        if cam_img:
            img_file = cam_img
    with tab_upload:
        up_img = st.file_uploader(
            "Or upload an image", type=["jpg", "jpeg", "png", "webp"]
        )
        if up_img:
            img_file = up_img

    if img_file and st.button("Extract Macros from Label", type="primary"):
        with st.spinner("Parsing label..."):
            st.session_state["extracted"] = parse_nutrition_label(img_file.getvalue())

    if "extracted" in st.session_state:
        ext: NutritionExtraction = st.session_state["extracted"]
        st.info(f"Detected Serving Size: **{ext.serving_size}**")
        with st.form("confirm_log"):
            f_col1, f_col2 = st.columns([2, 1])
            item_name = f_col1.text_input("Food Name", value=ext.item_name)
            serving_size_str = f_col2.text_input("Serving Size", value=ext.serving_size)
            servings = st.number_input(
                "Number of Servings Eaten", value=1.0, min_value=0.1, step=0.25
            )

            c1, c2, c3, c4 = st.columns(4)
            base_kcal = c1.number_input("Kcal / serv", value=ext.calories)
            base_pro = c2.number_input("Protein (g) / serv", value=ext.protein_g)
            base_carb = c3.number_input("Carbs (g) / serv", value=ext.carbs_g)
            base_fat = c4.number_input("Fat (g) / serv", value=ext.fat_g)

            save_to_staples = st.checkbox("Save to my Quick-Add Staples", value=True)

            if st.form_submit_button("Log Food", type="primary"):
                if save_to_staples and item_name.strip():
                    save_staple(
                        active_user["id"],
                        item_name,
                        serving_size_str,
                        base_kcal,
                        base_pro,
                        base_carb,
                        base_fat,
                    )
                log_food_entry(
                    active_user["id"],
                    today_str,
                    item_name,
                    servings,
                    base_kcal,
                    base_pro,
                    base_carb,
                    base_fat,
                )
                del st.session_state["extracted"]
                st.rerun()

    # --- TODAY'S LOG TABLE ---
    if not df.is_empty():
        st.subheader("Today's Logged Intake")
        st.dataframe(df.drop("id"), use_container_width=True, hide_index=True)
        if st.button("Undo Last Logged Item"):
            last_id = int(df["id"].max())
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "DELETE FROM logs WHERE id = ? AND user_id = ?",
                    (last_id, active_user["id"]),
                )
            st.rerun()

# ==========================================
# TAB 2: 7-DAY POLARS TRENDS
# ==========================================
with tab_history:
    st.subheader("Past 7 Days: Kilocalorie & Protein Adherence")
    df_7d = get_7day_summary(active_user, today_obj)

    avg_kcal = int(df_7d["Kcal"].mean() or 0)
    avg_pro = float(df_7d["Protein (g)"].mean() or 0.0)
    days_hit = int(df_7d["Goal Hit"].sum())

    k1, k2, k3 = st.columns(3)
    k1.metric(
        "7-Day Avg Kilocalories",
        f"{avg_kcal} kcal",
        f"{avg_kcal - active_user['kcal']:+d} vs target",
    )
    k2.metric(
        "7-Day Avg Protein",
        f"{avg_pro:.1f} g",
        f"{avg_pro - active_user['protein_g']:+.1f}g vs target",
    )
    k3.metric("Days Goal Hit (Kcal + P)", f"{days_hit} / 7 days")

    st.divider()

    st.markdown("**Daily Kilocalories vs. Target**")
    kcal_chart_df = df_7d.select(["Date", "Kcal", "Target Kcal"])
    st.bar_chart(kcal_chart_df, x="Date", y=["Kcal", "Target Kcal"], stack=False)

    st.markdown("**Daily Protein (g) vs. Target**")
    pro_chart_df = df_7d.select(["Date", "Protein (g)", "Target Protein (g)"])
    st.bar_chart(
        pro_chart_df, x="Date", y=["Protein (g)", "Target Protein (g)"], stack=False
    )

    st.subheader("7-Day Polars Breakdown")
    st.dataframe(
        df_7d.select(
            [
                "Date",
                "Kcal",
                "Kcal %",
                "Protein (g)",
                "Protein %",
                "Carbs (g)",
                "Fat (g)",
                "Goal Hit",
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )