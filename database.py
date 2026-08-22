import json
import os
import asyncpg
import logging
import datetime
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://botuser:botpass@db:5432/botdb")
logger = logging.getLogger(__name__)

user_history = {}
paused_users = {}
active_alerts = {}
user_metadata = {}
actual_shifts_data = []
muted_admins = []
custom_vacancies = {}

SHIFT_SCHEDULE = [
    {"start": 0,  "end": 8,  "emoji": "🌙", "label": "00:00 - 08:00", "key": "00-08"},
    {"start": 8,  "end": 16, "emoji": "☀️", "label": "08:00 - 16:00", "key": "08-16"},
    {"start": 16, "end": 24, "emoji": "🌆", "label": "16:00 - 00:00", "key": "16-00"},
]

bot_config = {
    "is_active": True, 
    "default_deadline": "[Generic Template Field]",
    "chatter_conditions": "",
    "chatter_form": "",
    "chatter_kb": ""
}

db_pool = None

def safe_json(data, default_type="dict"):
    empty_val = {} if default_type == "dict" else []
    if not data or data == "null":
        return empty_val
    try:
        res = json.loads(data)
        if default_type == "dict" and not isinstance(res, dict):
            return empty_val
        if default_type == "list" and not isinstance(res, list):
            return empty_val
        return res if res is not None else empty_val
    except Exception:
        return empty_val

async def init_db():
    global db_pool
    logger.info("[Generic Template Field]")
    db_pool = await asyncpg.create_pool(DATABASE_URL, command_timeout=10, max_size=30)
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_state (
                user_id BIGINT PRIMARY KEY,
                history JSONB DEFAULT '[]'::jsonb,
                paused BOOLEAN DEFAULT FALSE,
                alerts JSONB DEFAULT '[]'::jsonb,
                metadata JSONB DEFAULT '{"stage": "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).", "vacancy": "[Generic Template Field]", "conditions_sent": false, "form_sent": false}'::jsonb,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT
            )
        ''')

        rows = await conn.fetch('SELECT user_id, history, paused, alerts, metadata, updated_at FROM bot_state')
        now = datetime.datetime.now()
        for row in rows:
            uid = row['user_id']
            meta = safe_json(row['metadata'], "dict")
            
            updated_at = row['updated_at']
            if updated_at:
                updated_at = updated_at.replace(tzinfo=None)
                
            is_timeout = meta.get("is_timeout", False)
            days_inactive = (now - updated_at).days if updated_at else 0
            
            has_interview = bool(meta.get("interview_time")) and not meta.get("interview_reminded")
            
            if (days_inactive >= 7 or is_timeout) and not has_interview:
                continue
                
            paused_users[uid] = row['paused']
            active_alerts[uid] = safe_json(row['alerts'], "list")

            meta.setdefault("conditions_sent", False)
            meta.setdefault("form_sent", False)
            meta.setdefault("verify_sent", False)
            meta.setdefault("stage", "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).")
            meta.setdefault("vacancy", "[Generic Template Field]")
            meta.setdefault("desired_shift", "[Generic Template Field]")
            meta.setdefault("has_experience", False)
            meta.setdefault("is_timeout", False)
            meta.setdefault("followup_count", 0)
            
            user_metadata[uid] = meta
            user_history[uid] = safe_json(row['history'], "list")
                
        shift_row = await conn.fetchrow("SELECT setting_value FROM bot_settings WHERE setting_key = 'shifts'")
        if shift_row and shift_row['setting_value']:
            try: 
                parsed = json.loads(shift_row['setting_value'])
                if isinstance(parsed, list):
                    actual_shifts_data.clear()
                    actual_shifts_data.extend(parsed)
            except Exception: pass
            
        muted_row = await conn.fetchrow("SELECT setting_value FROM bot_settings WHERE setting_key = 'muted_admins'")
        if muted_row and muted_row['setting_value']:
            try: 
                parsed = json.loads(muted_row['setting_value'])
                if isinstance(parsed, list):
                    muted_admins.clear()
                    muted_admins.extend(parsed)
            except Exception: pass
            
        vac_row = await conn.fetchrow("SELECT setting_value FROM bot_settings WHERE setting_key = 'custom_vacancies'")
        if vac_row and vac_row['setting_value']:
            try: 
                parsed = json.loads(vac_row['setting_value'])
                if isinstance(parsed, dict):
                    custom_vacancies.clear()
                    custom_vacancies.update(parsed)
            except Exception: pass
            
        state_row = await conn.fetchrow("SELECT setting_value FROM bot_settings WHERE setting_key = 'global_bot_state'")
        if state_row and state_row['setting_value'] == 'off':
            bot_config["is_active"] = False
            
        for key in ["default_deadline", "chatter_conditions", "chatter_form", "chatter_kb"]:
            row = await conn.fetchrow("SELECT setting_value FROM bot_settings WHERE setting_key = $1", key)
            if row and row['setting_value']:
                bot_config[key] = row['setting_value']

async def save_state(user_id):
    paused_bool = paused_users.get(user_id, False)
    alerts_json = json.dumps(active_alerts.get(user_id, []))
    
    meta = user_metadata.get(user_id)
    if meta is None:
        meta = {}
        user_metadata[user_id] = meta
    meta.setdefault("stage", "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).")
    meta.setdefault("vacancy", "[Generic Template Field]")
    meta.setdefault("conditions_sent", False)
    meta.setdefault("form_sent", False)
    meta.setdefault("verify_sent", False)
    meta.setdefault("desired_shift", "[Generic Template Field]")
    meta.setdefault("has_experience", False)
    meta.setdefault("is_timeout", False)
    meta.setdefault("followup_count", 0)
    
    meta_json = json.dumps(meta)
    
    history_json = json.dumps(user_history[user_id]) if user_id in user_history else None

    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO bot_state (user_id, history, paused, alerts, metadata, updated_at)
                VALUES ($1, COALESCE($2::jsonb, '[]'::jsonb), $3, $4::jsonb, $5::jsonb, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO UPDATE SET
                    history = COALESCE($2::jsonb, bot_state.history),
                    paused = EXCLUDED.paused,
                    alerts = EXCLUDED.alerts,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
            ''', user_id, history_json, paused_bool, alerts_json, meta_json)
    except Exception as e:
        logger.error(f"An error occurred. Please try again later.", exc_info=True)

async def ensure_user_loaded(user_id):
    if user_id not in user_metadata:
        try:
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow('SELECT history, paused, alerts, metadata FROM bot_state WHERE user_id = $1', user_id)
                if row:
                    user_history[user_id] = safe_json(row['history'], "list")
                    paused_users[user_id] = row['paused']
                    active_alerts[user_id] = safe_json(row['alerts'], "list")
                    
                    meta = safe_json(row['metadata'], "dict")
                    meta.setdefault("stage", "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).")
                    meta.setdefault("vacancy", "[Generic Template Field]")
                    meta.setdefault("is_timeout", False)
                    meta.setdefault("conditions_sent", False)
                    meta.setdefault("form_sent", False)
                    meta.setdefault("verify_sent", False)
                    meta.setdefault("desired_shift", "[Generic Template Field]")
                    meta.setdefault("has_experience", False)
                    meta.setdefault("followup_count", 0)
                    user_metadata[user_id] = meta
                else:
                    user_history[user_id] = []
                    user_metadata[user_id] = {}
        except Exception as e:
            logger.error(f"An error occurred. Please try again later.")
            user_history[user_id] = []
            user_metadata[user_id] = {}

async def get_reserve_users():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, metadata, updated_at FROM bot_state WHERE metadata->>'is_timeout' = 'true'")
        return [(row['user_id'], safe_json(row['metadata'], "dict"), row['updated_at']) for row in rows]

async def save_shifts_to_db(data_list):
    data_to_save = list(data_list)
    actual_shifts_data.clear()
    actual_shifts_data.extend(data_to_save)
    json_str = json.dumps(data_to_save, ensure_ascii=False)
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (setting_key, setting_value) VALUES ('shifts', $1) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", json_str)

async def save_muted_admins():
    json_str = json.dumps(muted_admins)
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (setting_key, setting_value) VALUES ('muted_admins', $1) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", json_str)

async def save_custom_vacancies():
    json_str = json.dumps(custom_vacancies, ensure_ascii=False)
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (setting_key, setting_value) VALUES ('custom_vacancies', $1) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", json_str)

async def delete_user_from_db(user_id):
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM bot_state WHERE user_id = $1", user_id)

async def clear_reserve_users():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM bot_state WHERE metadata->>'is_timeout' = 'true'")
        for r in rows:
            uid = r['user_id']
            if uid in user_metadata: del user_metadata[uid]
            if uid in user_history: del user_history[uid]
            if uid in paused_users: del paused_users[uid]
        await conn.execute("DELETE FROM bot_state WHERE metadata->>'is_timeout' = 'true'")

async def delete_all_users_from_db():
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM bot_state")
    user_history.clear()
    paused_users.clear()
    active_alerts.clear()
    user_metadata.clear()

async def get_all_states():
    async with db_pool.acquire() as conn:
        return await conn.fetch("SELECT user_id, updated_at, paused, alerts, metadata->>'stage' as stage, metadata->>'is_timeout' as is_timeout, metadata->>'followup_count' as followup_count FROM bot_state")

async def set_bot_active(state: bool):
    bot_config["is_active"] = state
    val = 'on' if state else 'off'
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (setting_key, setting_value) VALUES ('global_bot_state', $1) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", val)

async def save_bot_config_text(key: str, text: str):
    bot_config[key] = text
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO bot_settings (setting_key, setting_value) VALUES ($1, $2) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value", key, text)