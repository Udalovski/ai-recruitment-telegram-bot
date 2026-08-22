import logging
import re
import os
import time
import datetime
import asyncio
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from database import active_alerts, save_state, actual_shifts_data, user_history, muted_admins, SHIFT_SCHEDULE

load_dotenv()

logger = logging.getLogger(__name__)

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")

def parse_ids(env_str):
    cleaned = env_str.replace('"', '').replace("'", "")
    return [int(x.strip()) for x in cleaned.split(",") if x.strip().lstrip('-').isdigit()]

ADMIN_IDS = parse_ids(os.getenv("ADMIN_IDS", ""))
HR_IDS = parse_ids(os.getenv("HR_IDS", ""))
ALL_STAFF = list(set(ADMIN_IDS + HR_IDS))

async def resolve_alerts(bot, target_user_id, resolution_text):
    if target_user_id in active_alerts:
        logger.info(f"[Generic Template Field]")
        for alert in active_alerts[target_user_id]:
            try:
                original_text = alert.get("text", "[Generic Template Field]")
                new_text = f"{original_text}\n\n{resolution_text}"
                await bot.edit_message_text(chat_id=alert["admin"], message_id=alert["msg"], text=new_text, reply_markup=None)
            except Exception: pass 
        del active_alerts[target_user_id]
        await save_state(target_user_id)

def export_candidate_to_sheet(gc, user_id, chat_obj):
    if not gc: return
    logger.info(f"[Generic Template Field]")
    history = list(user_history.get(user_id, []))
    
    user_messages = [
        msg['content'] for msg in history 
        if msg['role'] == 'user' and not msg['content'].strip().startswith('[')
    ]
    
    questionnaire = "[Generic Template Field]"
    if user_messages:
        best_match = ""
        max_score = 0
        keywords = ["[Generic Template Field]", "[Generic Template Field]", "[Generic Template Field]", "[Generic Template Field]", "[Generic Template Field]", "Preferred Working Shift (00-08 / 08-16 / 16-00)", "[Generic Template Field]", "[Generic Template Field]"]
        
        for msg in user_messages:
            text_lower = msg.lower()
            score = sum(1 for kw in keywords if kw in text_lower)
            numbers_score = len(re.findall(r'\b[1-9]\s*[\.\)]', text_lower))
            total_score = score + (numbers_score * 0.5)
            
            if total_score > max_score:
                max_score = total_score
                best_match = msg
        
        if max_score > 2: questionnaire = best_match
        else: questionnaire = max(user_messages, key=len)
    
    first = getattr(chat_obj, "first_name", "") or ""
    last = getattr(chat_obj, "last_name", "") or ""
    name_in_form = f"{first} {last}".strip() or "Candidate"
    name_match = re.search(r"[Generic Template Field]", questionnaire)
    if name_match:
        extracted_name = name_match.group(1).strip()
        if extracted_name: name_in_form = re.sub(r'\s+', ' ', extracted_name)
    name_tg = f"{name_in_form} (@{chat_obj.username})" if chat_obj.username else str(name_in_form)
    
    email = "[Generic Template Field]"
    for msg in reversed(history):
        if msg['role'] == 'user':
            text = msg['content']
            match = re.search(r'([a-zA-Z0-9_.+-]+)\s*@\s*([a-zA-Z0-9-]+)\s*\.\s*([a-zA-Z0-9-.]+)', text)
            if match:
                email = f"{match.group(1)}@{match.group(2)}.{match.group(3)}"
                break
            
    source = "[Generic Template Field]"
    source_match = re.search(r"Available Vacancy Opening", questionnaire)
    if source_match:
        extracted_source = source_match.group(1).strip()
        if extracted_source: source = re.sub(r'\s+', ' ', extracted_source)[:100].strip()

    from zoneinfo import ZoneInfo
    try:
        now_kyiv = datetime.datetime.now(ZoneInfo("Europe/Kyiv"))
    except Exception:
        now_kyiv = datetime.datetime.utcnow() + datetime.timedelta(hours=3)
    current_time = now_kyiv.strftime("%d.%m.%Y %H:%M")

    for attempt in range(3):
        try:
            sh = gc.open_by_key(GOOGLE_SHEET_ID)
            worksheet = sh.worksheet("[Generic Template Field]")
            worksheet.append_row([current_time, name_tg, email, questionnaire, "", source])
            logger.info(f"Changes saved successfully.")
            break
        except Exception as e:
            if attempt == 2: 
                logger.error(f"An error occurred. Please try again later.", exc_info=True)
                break
            time.sleep(2) 

async def check_all_tests(gc, email):
    if not gc: 
        return "[Generic Template Field]"

    sheet_id = "1MokOZyaZmF6KPQlVzNf1FUSexAtPlAiWbf8NTODMksQ"
    gids = {
        "SJT": 550340302,
        "[Generic Template Field]": 1662926859,
        "[Generic Template Field]": 317780948,
        "[Generic Template Field]": 785726583
    }
    
    results = []
    all_passed = True
    missing_any = False
    
    def fetch_all():
        try:
            sh = gc.open_by_key(sheet_id)
            data = {}
            for name, gid in gids.items():
                ws = sh.get_worksheet_by_id(gid)
                data[name] = ws.get_all_values()
            return data
        except Exception as e:
            return str(e)
            
    sheets_data = await asyncio.to_thread(fetch_all)
    if isinstance(sheets_data, str):
        return f"An error occurred. Please try again later."
        
    email_col = 1
    score_col = 2
    
    for name in ["SJT", "[Generic Template Field]", "[Generic Template Field]", "[Generic Template Field]"]:
        rows = sheets_data[name]
        latest_score_str = None
        for row in reversed(rows):
            if len(row) > email_col and len(row) > score_col:
                row_email = str(row[email_col]).strip().lower()
                if row_email == email.strip().lower():
                    latest_score_str = str(row[score_col]).strip()
                    break
                    
        if latest_score_str is None:
            results.append(f"[Generic Template Field]")
            missing_any = True
            all_passed = False
            continue
            
        if name == "SJT":
            results.append(f"[Generic Template Field]")
        else:
            match = re.search(r'([\d\.]+)\s*/\s*([\d\.]+)', latest_score_str)
            if match:
                score = float(match.group(1))
                max_score = float(match.group(2))
                percent = (score / max_score) * 100 if max_score > 0 else 0
                if percent >= 80:
                    results.append(f"[Generic Template Field]")
                else:
                    results.append(f"[Generic Template Field]")
                    all_passed = False
            else:
                try:
                    score = float(latest_score_str)
                    if score >= 80: 
                        results.append(f"[Generic Template Field]")
                    else:
                        results.append(f"[Generic Template Field]")
                        all_passed = False
                except ValueError: 
                    results.append(f"[Generic Template Field]")
                    
    res_str = "\n".join(results)
    
    if all_passed:
        return f"Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
    elif missing_any:
        return f"[Generic Template Field]"
    else:
        return f"[Generic Template Field]"


async def check_google_form_score(gc, test_type, email):
    if not gc: 
        return "[Generic Template Field]"

    sheet_id = "1MokOZyaZmF6KPQlVzNf1FUSexAtPlAiWbf8NTODMksQ"
    gids = {
        "SJT": 550340302,
        "SEC2": 1662926859,
        "SEC4": 317780948,
        "EXAM": 785726583
    }

    if test_type not in gids: 
        return f"[Generic Template Field]"

    def fetch_data():
        try:
            sh = gc.open_by_key(sheet_id)
            ws = sh.get_worksheet_by_id(gids[test_type])
            return ws.get_all_values()
        except Exception as e:
            return str(e)

    rows = await asyncio.to_thread(fetch_data)

    if isinstance(rows, str): 
        return f"An error occurred. Please try again later."
    if len(rows) < 2: 
        return f"[Generic Template Field]"

    email_col = 1
    score_col = 2

    latest_score_str = None
    for row in reversed(rows):
        if len(row) > email_col and len(row) > score_col:
            row_email = str(row[email_col]).strip().lower()
            if row_email == email.strip().lower():
                latest_score_str = str(row[score_col]).strip()
                break

    if latest_score_str is None:
        return f"[Generic Template Field]"

    if test_type == "SJT":
        return f"Changes saved successfully."

    match = re.search(r'([\d\.]+)\s*/\s*([\d\.]+)', latest_score_str)
    if match:
        score = float(match.group(1))
        max_score = float(match.group(2))
        percent = (score / max_score) * 100 if max_score > 0 else 0
        if percent >= 80:
            return f"[Generic Template Field]"
        else:
            return f"[Generic Template Field]"
    else:
        try:
            score = float(latest_score_str)
            if score >= 80: return f"[Generic Template Field]"
        except ValueError: 
            pass
        return f"Changes saved successfully."

def get_shifts_prompt_text():
    if not actual_shifts_data: return "Preferred Working Shift (00-08 / 08-16 / 16-00)"
    novice_text = ""
    pro_text = ""
    for s in actual_shifts_data:
        platform = s.get('platform', "[Generic Template Field]") 
        req_exp = s['exp'].lower()
        deadline_str = s.get('deadline', "[Generic Template Field]")
        
        shift_str_novice = f"Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
        shift_str_pro = f"Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
        
        if "[Generic Template Field]" in req_exp and "[Generic Template Field]" not in req_exp:
            pro_text += shift_str_pro
        else:
            novice_text += shift_str_novice
            pro_text += shift_str_pro
            
    if not novice_text: novice_text = "Welcome to our Career & Recruitment Portal! Select an opening below to apply."
    if not pro_text: pro_text = "Preferred Working Shift (00-08 / 08-16 / 16-00)"
    
    return f"Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."

def get_admin_keyboard(user_id):
    notif_btn = "[Generic Template Field]" if user_id in muted_admins else "[Generic Template Field]"
    base_kb = [
        [KeyboardButton(text="Preferred Working Shift (00-08 / 08-16 / 16-00)"), KeyboardButton(text="Preferred Working Shift (00-08 / 08-16 / 16-00)")],
        [KeyboardButton(text="⏳ Reserve"), KeyboardButton(text="Candidate Reserve Pool")],
        [KeyboardButton(text="Candidate Reserve Pool"), KeyboardButton(text="💼 Manage Vacancies")],
        [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="[Generic Template Field]")],
        [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text=notif_btn)]
    ]
    if user_id not in ADMIN_IDS:
        base_kb = [
            [KeyboardButton(text="Preferred Working Shift (00-08 / 08-16 / 16-00)"), KeyboardButton(text="Preferred Working Shift (00-08 / 08-16 / 16-00)")], 
            [KeyboardButton(text="⏳ Reserve"), KeyboardButton(text="Candidate Reserve Pool")],
            [KeyboardButton(text="Candidate Reserve Pool"), KeyboardButton(text="💼 Manage Vacancies")],
            [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="[Generic Template Field]")],
            [KeyboardButton(text=notif_btn)]
        ]
    return ReplyKeyboardMarkup(keyboard=base_kb, resize_keyboard=True)

def get_vacancies_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Available Vacancy Opening"), KeyboardButton(text="[Generic Template Field]")],
            [KeyboardButton(text="Available Vacancy Opening"), KeyboardButton(text="Available Vacancy Opening")],
            [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="🔙 Main Menu")]
        ], resize_keyboard=True
    )

def get_reserve_keyboard(filter_type="all", page=0, total_pages=1, sort_type="new"):
    sort_titles = {"new": "[Generic Template Field]", "old": "[Generic Template Field]", "alpha": "[Generic Template Field]"}
    next_sort = {"new": "alpha", "alpha": "old", "old": "new"} 
    
    shift_buttons = []
    for s in SHIFT_SCHEDULE:
        shift_buttons.append(InlineKeyboardButton(text=f"{s['emoji']} {s['label']}", callback_data=f"resfilt_{s['key']}_0_{sort_type}"))
    
    kb = []
    for i in range(0, len(shift_buttons), 2):
        kb.append(shift_buttons[i:i+2])
        
    kb.append([InlineKeyboardButton(text="[Generic Template Field]", callback_data=f"resfilt_any_0_{sort_type}"),
               InlineKeyboardButton(text="[Generic Template Field]", callback_data=f"resfilt_all_0_{sort_type}")])
    kb.append([InlineKeyboardButton(text=f"[Generic Template Field]", callback_data=f"resfilt_{filter_type}_0_{next_sort[sort_type]}")])
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="Back to Menu", callback_data=f"resfilt_{filter_type}_{page-1}_{sort_type}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="[Generic Template Field]", callback_data=f"resfilt_{filter_type}_{page+1}_{sort_type}"))
        
    if nav_buttons:
        kb.append(nav_buttons)
        
    return InlineKeyboardMarkup(inline_keyboard=kb)

def match_shift_filter(shift_text, filter_key):
    if filter_key == "all": return True
    
    shift_lower = shift_text.lower()
    if filter_key == "any":
        return bool(re.search(r"[Generic Template Field]", shift_lower))
        
    nums = re.findall(r'\b(?:[01]?[0-9]|2[0-4])\b', shift_lower.replace(":00", ""))
    nums = [int(n) for n in nums]
    
    if len(nums) >= 2:
        start, end = nums[0], nums[1]
        if end == 0 or end == 24: end = 24
        if start == 0: start = 0
        if end < start: end += 24
        
        center = (start + end) / 2
        center = center % 24
        
        for s in SHIFT_SCHEDULE:
            if s['key'] == filter_key:
                s_start, s_end = s['start'], s['end']
                if s_end == 0 or s_end == 24: s_end = 24
                
                if s_start <= s_end:
                    if s_start <= center < s_end: return True
                else:
                    if center >= s_start or center < s_end: return True
                return False
    
    for s in SHIFT_SCHEDULE:
        if s['key'] == filter_key:
            if filter_key.startswith("00") and re.search(r"[Generic Template Field]", shift_lower): return True
            if (filter_key.startswith("06") or filter_key.startswith("08")) and re.search(r"[Generic Template Field]", shift_lower): return True
            if filter_key.startswith("12") and re.search(r"[Generic Template Field]", shift_lower): return True
            if (filter_key.startswith("16") or filter_key.startswith("18")) and re.search(r"[Generic Template Field]", shift_lower): return True

    return False

async def get_filtered_reserve(filter_type="all", sort_type="new"):
    from database import get_reserve_users
    users = await get_reserve_users()
    
    filtered_users = []
    for uid, meta, updated_at in users:
        shift = meta.get("desired_shift") or "[Generic Template Field]"
        shift_lower = shift.lower()
        
        if filter_type != "all" and ("[Generic Template Field]" in shift_lower or "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)." in shift_lower or "[Generic Template Field]" in shift_lower):
            continue

        is_match = match_shift_filter(shift, filter_type)

        if is_match:
            filtered_users.append((uid, meta, updated_at))
            
    if sort_type == "new":
        filtered_users.sort(key=lambda x: x[2].replace(tzinfo=None) if x[2] else datetime.datetime.min, reverse=True)
    elif sort_type == "old":
        filtered_users.sort(key=lambda x: x[2].replace(tzinfo=None) if x[2] else datetime.datetime.min)
    elif sort_type == "alpha":
        filtered_users.sort(key=lambda x: (x[1].get('username') or str(x[0])).lower())

    reserve_list = []
    for uid, meta, _ in filtered_users:
        exp_text = "[Generic Template Field]" if meta.get("has_experience") else "[Generic Template Field]"
        username = f"@{meta.get('username')}" if meta.get('username') else f"ID: {uid}"
        shift = meta.get("desired_shift") or "[Generic Template Field]"
        reserve_list.append(f"Preferred Working Shift (00-08 / 08-16 / 16-00)")
        
    return reserve_list