import re
import datetime
import logging
import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest

from database import (
    custom_vacancies, save_custom_vacancies, muted_admins, save_muted_admins,
    get_all_states, user_metadata, save_state, delete_user_from_db, delete_all_users_from_db,
    user_history, paused_users, active_alerts, actual_shifts_data, save_shifts_to_db,
    ensure_user_loaded, get_reserve_users, bot_config, set_bot_active, save_bot_config_text, SHIFT_SCHEDULE
)
from utils import (
    ALL_STAFF, ADMIN_IDS, get_vacancies_keyboard, get_admin_keyboard,
    get_filtered_reserve, get_reserve_keyboard, resolve_alerts, match_shift_filter
)

admin_router = Router()
logger = logging.getLogger(__name__)

class AdminStates(StatesGroup):
    waiting_for_shifts = State()
    waiting_for_delete = State()
    waiting_for_unreserve = State()
    waiting_for_vac_name = State()
    waiting_for_vac_cond = State()
    waiting_for_vac_form = State()
    waiting_for_vac_kb = State()
    waiting_for_vac_deadline = State()
    waiting_for_vac_final_msg = State()
    waiting_for_toggle_bot = State()
    waiting_for_edit_vac_field = State()
    waiting_for_edit_vac_value = State()
    waiting_for_edit_chatter_field = State()
    waiting_for_edit_chatter_value = State()
    waiting_for_edit_shift_field = State()
    waiting_for_edit_shift_value = State()
    waiting_for_broadcast_target = State()
    waiting_for_broadcast_message = State()
async def resolve_target_user(target: str):
    target_id = None
    if target.isdigit():
        target_id = int(target)
    else:
        for uid, meta in user_metadata.items():
            if meta.get("username", "").lower() == target.lower():
                target_id = uid
                break
        if not target_id:
            from database import db_pool
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT user_id FROM bot_state WHERE metadata->>'username' ILIKE $1", target)
                if row: target_id = row['user_id']
                
    if target_id:
        await ensure_user_loaded(target_id)
        if target_id in user_metadata: return target_id
    return None

@admin_router.message(F.text == "💼 Manage Vacancies")
async def cmd_vacancies_menu(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    await message.answer("[Generic Template Field]", reply_markup=get_vacancies_keyboard(), parse_mode="HTML")

@admin_router.message(F.text == "Available Vacancy Opening")
async def cmd_list_vacancies(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    if not custom_vacancies:
        await message.answer("Available Vacancy Opening", reply_markup=get_vacancies_keyboard())
        return
    text = "Available Vacancy Opening"
    for name, vac_data in custom_vacancies.items(): 
        deadline = vac_data.get('deadline', "[Generic Template Field]")
        cond_preview = vac_data.get('conditions', '').replace('\n', ' ')[:25]
        form_preview = vac_data.get('form', '').replace('\n', ' ')[:25]
        kb_preview = vac_data.get('kb', '').replace('\n', ' ')[:25]
        text += f"🔸 <b>{name}</b>\n"
        text += f"[Generic Template Field]"
        text += f"  📋 Application Form: {form_preview}...\n  🧠 FAQ: {kb_preview}...\n\n"
    await message.answer(text, parse_mode="HTML", reply_markup=get_vacancies_keyboard())

@admin_router.message(F.text == "Available Vacancy Opening")
async def cmd_del_vacancy(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    if not custom_vacancies:
        return await message.answer("Available Vacancy Opening", reply_markup=get_vacancies_keyboard())
        
    buttons = []
    for i, name in enumerate(custom_vacancies.keys()):
        buttons.append([InlineKeyboardButton(text=f"❌ {name}", callback_data=f"delvac_{i}")])
        
    await message.answer("Available Vacancy Opening", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@admin_router.callback_query(F.data.startswith("delvac_"))
async def process_delvac_inline(callback: CallbackQuery):
    if callback.from_user.id not in ALL_STAFF: return await callback.answer("[Generic Template Field]", show_alert=True)
    idx_str = callback.data.split("_", 1)[1]
    
    if not idx_str.isdigit():
        return await callback.answer("An error occurred. Please try again later.", show_alert=True)
    
    idx = int(idx_str)
    vac_names = list(custom_vacancies.keys())
    
    if idx < len(vac_names):
        name = vac_names[idx]
        del custom_vacancies[name]
        await save_custom_vacancies()
        logger.info(f"Available Vacancy Opening")
        await callback.message.edit_text(f"Record deleted.", parse_mode="HTML")
    else:
        await callback.answer("[Generic Template Field]", show_alert=True)

@admin_router.message(F.text == "[Generic Template Field]")
async def cmd_add_vacancy(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    await state.set_state(AdminStates.waiting_for_vac_name)
    await message.answer("Available Vacancy Opening", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel")]], resize_keyboard=True))

@admin_router.message(AdminStates.waiting_for_vac_name)
async def process_vac_name(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        return
        
    vac_name = message.text.strip()
    found_key = next((k for k in custom_vacancies if k.lower() == vac_name.lower()), None)
    if found_key:
        return await message.answer(f"Cancel", parse_mode="HTML")
        
    await state.update_data(vac_name=vac_name)
    await state.set_state(AdminStates.waiting_for_vac_cond)
    await message.answer("Available Vacancy Opening", parse_mode="HTML")

@admin_router.message(AdminStates.waiting_for_vac_cond)
async def process_vac_cond(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        return
        
    await state.update_data(vac_cond=message.text)
    await state.set_state(AdminStates.waiting_for_vac_form)
    await message.answer("Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).", parse_mode="HTML")

@admin_router.message(AdminStates.waiting_for_vac_form)
async def process_vac_form(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        return
        
    await state.update_data(vac_form=message.text)
    await state.set_state(AdminStates.waiting_for_vac_kb)
    await message.answer("Available Vacancy Opening", parse_mode="HTML")

@admin_router.message(AdminStates.waiting_for_vac_kb)
async def process_vac_kb(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        return
        
    await state.update_data(vac_kb=message.text)
    await state.set_state(AdminStates.waiting_for_vac_deadline)
    await message.answer("Available Vacancy Opening", parse_mode="HTML")

@admin_router.message(AdminStates.waiting_for_vac_deadline)
async def process_vac_deadline(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        return
        
    await state.update_data(vac_deadline=message.text.strip())
    await state.set_state(AdminStates.waiting_for_vac_final_msg)
    await message.answer("Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).", parse_mode="HTML")

@admin_router.message(AdminStates.waiting_for_vac_final_msg)
async def process_vac_final_msg(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        return
        
    data = await state.get_data()
    name = data['vac_name']
    
    custom_vacancies[name] = {
        "conditions": data['vac_cond'], 
        "form": data['vac_form'],
        "kb": data['vac_kb'],
        "deadline": data['vac_deadline'],
        "final_msg": message.text.strip()
    }
    
    await save_custom_vacancies()
    logger.info(f"Available Vacancy Opening")
    await state.clear()
    await message.answer(f"Changes saved successfully.", parse_mode="HTML", reply_markup=get_vacancies_keyboard())

@admin_router.message(F.text == "Available Vacancy Opening")
async def cmd_edit_custom_vacancy(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    if not custom_vacancies:
        return await message.answer("Available Vacancy Opening", reply_markup=get_vacancies_keyboard())
        
    buttons = []
    for i, name in enumerate(custom_vacancies.keys()):
        buttons.append([InlineKeyboardButton(text=f"✏️ {name}", callback_data=f"editvac_{i}")])
        
    await message.answer("Available Vacancy Opening", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@admin_router.callback_query(F.data.startswith("editvac_"))
async def process_editvac_inline(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALL_STAFF: return await callback.answer("[Generic Template Field]", show_alert=True)
    idx_str = callback.data.split("_", 1)[1]
    
    if not idx_str.isdigit():
        return await callback.answer("An error occurred. Please try again later.", show_alert=True)
        
    idx = int(idx_str)
    vac_names = list(custom_vacancies.keys())
    
    if idx >= len(vac_names): 
        return await callback.answer("[Generic Template Field]", show_alert=True)
        
    name = vac_names[idx]
    
    await state.update_data(edit_vac_name=name)
    await state.set_state(AdminStates.waiting_for_edit_vac_field)
    
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="📋 Application Form")],
            [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="[Generic Template Field]")],
            [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="Cancel")]
        ], resize_keyboard=True
    )
    await callback.message.delete()
    await callback.message.answer(f"Available Vacancy Opening", reply_markup=kb, parse_mode="HTML")

@admin_router.message(AdminStates.waiting_for_edit_vac_field)
async def process_edit_vac_field(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text: return await message.answer("[Generic Template Field]")
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        return await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        
    field_map = {"[Generic Template Field]": "conditions", "📋 Application Form": "form", "[Generic Template Field]": "kb", "[Generic Template Field]": "deadline", "[Generic Template Field]": "final_msg"}
    
    if message.text not in field_map:
        return await message.answer("[Generic Template Field]")
        
    field_key = field_map[message.text]
    await state.update_data(edit_vac_field=field_key)
    await state.set_state(AdminStates.waiting_for_edit_vac_value)
    
    data = await state.get_data()
    vac_name = data['edit_vac_name']
    current_value = custom_vacancies[vac_name].get(field_key, "[Generic Template Field]")
    if len(current_value) > 3000: current_value = current_value[:3000] + "[Generic Template Field]"
    safe_value = current_value.replace("<", "&lt;").replace(">", "&gt;")
    
    await message.answer(f"[Generic Template Field]", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel")]], resize_keyboard=True))

@admin_router.message(AdminStates.waiting_for_edit_vac_value)
async def process_edit_vac_value(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text: return await message.answer("[Generic Template Field]")
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        return await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        
    data = await state.get_data()
    vac_name = data['edit_vac_name']
    field_key = data['edit_vac_field']
    
    custom_vacancies[vac_name][field_key] = message.text.strip()
    await save_custom_vacancies()
    field_names = {"conditions": "[Generic Template Field]", "form": "Application Form", "kb": "[Generic Template Field]", "deadline": "[Generic Template Field]", "final_msg": "[Generic Template Field]"}
    await state.clear()
    await message.answer(f"Available Vacancy Opening", parse_mode="HTML", reply_markup=get_vacancies_keyboard())

@admin_router.message(F.text == "[Generic Template Field]")
async def cmd_edit_chatter_vacancy(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    
    text = "Preferred Working Shift (00-08 / 08-16 / 16-00)"
    shift_buttons = []
    
    if actual_shifts_data:
        text += "Preferred Working Shift (00-08 / 08-16 / 16-00)"
        for i, s in enumerate(actual_shifts_data):
            platform = s.get('platform', "[Generic Template Field]")
            text += f"[Generic Template Field]"
            text += f"Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
            text += f"[Generic Template Field]"
            text += f"[Generic Template Field]"
            text += f"[Generic Template Field]"
            text += "──────────────\n"
            shift_buttons.append(InlineKeyboardButton(text=f"[Generic Template Field]", callback_data=f"editshift_{i}"))
    else:
        text += "Preferred Working Shift (00-08 / 08-16 / 16-00)"
        
    keyboard = [shift_buttons[i:i+2] for i in range(0, len(shift_buttons), 2)]
    keyboard.insert(0, [InlineKeyboardButton(text="Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).", callback_data="edit_chatter_texts")])
        
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard), parse_mode="HTML")

@admin_router.callback_query(F.data == "edit_chatter_texts")
async def process_edit_chatter_texts(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALL_STAFF: return await callback.answer("[Generic Template Field]", show_alert=True)
    await state.set_state(AdminStates.waiting_for_edit_chatter_field)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="📋 Application Form")], [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="Cancel")]], resize_keyboard=True)
    await callback.message.delete()
    await callback.message.answer("Available Vacancy Opening", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("editshift_"))
async def process_edit_shift(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ALL_STAFF: return await callback.answer("[Generic Template Field]", show_alert=True)
    idx = int(callback.data.split("_")[1])
    if idx >= len(actual_shifts_data): return await callback.answer("Preferred Working Shift (00-08 / 08-16 / 16-00)", show_alert=True)
    
    await state.update_data(edit_shift_idx=idx)
    await state.set_state(AdminStates.waiting_for_edit_shift_field)
    
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="[Generic Template Field]")],
            [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="[Generic Template Field]")],
            [KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="Cancel")]
        ], resize_keyboard=True
    )
    shift = actual_shifts_data[idx]
    await callback.message.delete()
    await callback.message.answer(f"Preferred Working Shift (00-08 / 08-16 / 16-00)", reply_markup=kb, parse_mode="HTML")

@admin_router.message(AdminStates.waiting_for_edit_chatter_field)
async def process_edit_chatter_field(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text: return await message.answer("[Generic Template Field]")
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        return await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        
    field_map = {"[Generic Template Field]": "chatter_conditions", "📋 Application Form": "chatter_form", "[Generic Template Field]": "default_deadline"}
    if message.text not in field_map: return await message.answer("[Generic Template Field]")
        
    field_key = field_map[message.text]
    await state.update_data(edit_chatter_field=field_key)
    await state.set_state(AdminStates.waiting_for_edit_chatter_value)
    
    current_value = bot_config.get(field_key, "")
    if not current_value:
        if field_key == "chatter_conditions":
            default_text = "Available Vacancy Opening"
        elif field_key == "chatter_form":
            default_text = "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
        elif field_key == "chatter_kb":
            try:
                with open("knowledge_base.txt", "r", encoding="utf-8") as f:
                    default_text = f.read()
            except Exception:
                default_text = "[Generic Template Field]"
        else:
            default_text = "[Generic Template Field]"
        current_value = f"[Generic Template Field]"
        
    if len(current_value) > 3000: current_value = current_value[:3000] + "[Generic Template Field]"
    
    safe_value = current_value.replace("<", "&lt;").replace(">", "&gt;")
    await message.answer(f"[Generic Template Field]", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="[Generic Template Field]")], [KeyboardButton(text="Cancel")]], resize_keyboard=True))

@admin_router.message(AdminStates.waiting_for_edit_chatter_value)
async def process_edit_chatter_value(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text: return await message.answer("[Generic Template Field]")
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        return await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        
    data = await state.get_data()
    
    if message.text == "[Generic Template Field]":
        await save_bot_config_text(data['edit_chatter_field'], "")
        await state.clear()
        return await message.answer("Changes saved successfully.", reply_markup=get_vacancies_keyboard())
        
    await save_bot_config_text(data['edit_chatter_field'], message.text.strip())
    await state.clear()
    await message.answer(f"Changes saved successfully.", parse_mode="HTML", reply_markup=get_vacancies_keyboard())

@admin_router.message(AdminStates.waiting_for_edit_shift_field)
async def process_edit_shift_field(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text: return await message.answer("[Generic Template Field]")
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        return await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        
    field_map = {"[Generic Template Field]": "name", "[Generic Template Field]": "top", "[Generic Template Field]": "total", "[Generic Template Field]": "time", "[Generic Template Field]": "exp", "[Generic Template Field]": "platform", "[Generic Template Field]": "deadline"}
    if message.text not in field_map: return await message.answer("[Generic Template Field]")
        
    field_key = field_map[message.text]
    await state.update_data(edit_shift_field=field_key)
    await state.set_state(AdminStates.waiting_for_edit_shift_value)
    
    data = await state.get_data()
    idx = data['edit_shift_idx']
    
    if idx >= len(actual_shifts_data):
        await state.clear()
        return await message.answer("Preferred Working Shift (00-08 / 08-16 / 16-00)", reply_markup=get_vacancies_keyboard())
        
    current_value = actual_shifts_data[idx].get(field_key, "[Generic Template Field]")
    safe_value = str(current_value).replace("<", "&lt;").replace(">", "&gt;")
    await message.answer(f"[Generic Template Field]", parse_mode="HTML", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel")]], resize_keyboard=True))

@admin_router.message(AdminStates.waiting_for_edit_shift_value)
async def process_edit_shift_value(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text: return await message.answer("[Generic Template Field]")
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        return await message.answer("Cancel", reply_markup=get_vacancies_keyboard())
        
    data = await state.get_data()
    idx = data['edit_shift_idx']
    field_key = data['edit_shift_field']
    
    if idx < len(actual_shifts_data):
        actual_shifts_data[idx][field_key] = message.text.strip()
        await save_shifts_to_db(actual_shifts_data)
        await message.answer(f"Preferred Working Shift (00-08 / 08-16 / 16-00)", reply_markup=get_vacancies_keyboard())
    else:
        await message.answer("Preferred Working Shift (00-08 / 08-16 / 16-00)", reply_markup=get_vacancies_keyboard())
    await state.clear()

@admin_router.message(F.text == "🔙 Main Menu")
async def back_to_main_menu_vac(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ALL_STAFF: return
    await message.answer("[Generic Template Field]", reply_markup=get_admin_keyboard(message.from_user.id))

@admin_router.message(F.text == "/admin")
async def cmd_admin_menu(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ALL_STAFF: return
    kb = get_admin_keyboard(message.from_user.id)
    role = "[Generic Template Field]" if message.from_user.id in ADMIN_IDS else "HR"
    await message.answer(f"[Generic Template Field]", reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "Cancel")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    kb = get_admin_keyboard(message.from_user.id)
    await message.answer("Cancel", reply_markup=kb)

@admin_router.message((F.text == "[Generic Template Field]") | (F.text == "[Generic Template Field]"))
async def toggle_notifications(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    
    if message.from_user.id in muted_admins:
        muted_admins.remove(message.from_user.id)
        text = "An error occurred. Please try again later."
        logger.info(f"[Generic Template Field]")
    else:
        muted_admins.append(message.from_user.id)
        text = "[Generic Template Field]"
        logger.info(f"[Generic Template Field]")
        
    await save_muted_admins()
    kb = get_admin_keyboard(message.from_user.id)
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@admin_router.message(F.text == "[Generic Template Field]")
async def cmd_statistics(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    
    rows = await get_all_states()
    now = datetime.datetime.now()
    
    stats = {
        "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).": 0,
        "[Generic Template Field]": 0,
        "[Generic Template Field]": 0,
        "[Generic Template Field]": 0
    }
    total_active = 0
    
    for row in rows:
        stage_raw = (row['stage'] or "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).").lower()
        updated_at = row['updated_at']
        
        alerts_json = row['alerts'] or '[]'
        has_interview_alert = "[Generic Template Field]" in alerts_json
        
        if not updated_at:
            updated_at = now 
        else:
            updated_at = updated_at.replace(tzinfo=None)
            
        days_inactive = (now - updated_at).days
        mapped_stage = None
        
        if "[Generic Template Field]" in stage_raw or "[Generic Template Field]" in stage_raw:
            if has_interview_alert: mapped_stage = "[Generic Template Field]"
            else: continue 
        elif "[Generic Template Field]" in stage_raw or "[Generic Template Field]" in stage_raw: mapped_stage = "[Generic Template Field]"
        elif "[Generic Template Field]" in stage_raw or "[Generic Template Field]" in stage_raw or "[Generic Template Field]" in stage_raw: mapped_stage = "[Generic Template Field]"
        elif "[Generic Template Field]" in stage_raw: continue
        else: mapped_stage = "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
            
        if not mapped_stage: continue
        if mapped_stage == "[Generic Template Field]" and days_inactive > 4: continue
        if mapped_stage != "[Generic Template Field]" and days_inactive > 3: continue
            
        stats[mapped_stage] += 1
        total_active += 1
        
    if total_active == 0:
        await message.answer("[Generic Template Field]")
        return
        
    text = f"[Generic Template Field]"
    for s in ["Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).", "[Generic Template Field]", "[Generic Template Field]", "[Generic Template Field]"]:
        text += f"[Generic Template Field]"
    text += "[Generic Template Field]"
    await message.answer(text, parse_mode="HTML")

@admin_router.message(F.text == "⏳ Reserve")
async def cmd_show_reserve(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    reserve_list = await get_filtered_reserve("all", "new")
    if not reserve_list:
        await message.answer("Candidate Reserve Pool")
        return
        
    total_pages = (len(reserve_list) + 59) // 60
    page_data = reserve_list[:60]
    
    text = f"Candidate Reserve Pool" + "\n".join(page_data)
    if total_pages > 1: text += f"[Generic Template Field]"
    else: text += "[Generic Template Field]"
    
    await message.answer(text, parse_mode="HTML", reply_markup=get_reserve_keyboard("all", 0, total_pages, "new"))

@admin_router.callback_query(F.data.startswith("resfilt_"))
async def process_reserve_filter(callback: CallbackQuery):
    if callback.from_user.id not in ALL_STAFF: return await callback.answer("[Generic Template Field]", show_alert=True)
    
    parts = callback.data.split("_")
    filter_type = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    sort_type = parts[3] if len(parts) > 3 else "new"
    
    reserve_list = await get_filtered_reserve(filter_type, sort_type)
    titles = {"all": "[Generic Template Field]", "any": "Preferred Working Shift (00-08 / 08-16 / 16-00)"}
    for s in SHIFT_SCHEDULE:
        titles[s['key']] = f"Shift {s['label']}"
    
    if not reserve_list:
        text = f"Candidate Reserve Pool"
        total_pages = 1
    else:
        total_pages = (len(reserve_list) + 59) // 60
        if page >= total_pages: page = total_pages - 1
        if page < 0: page = 0
        
        start_idx = page * 60
        end_idx = start_idx + 60
        page_data = reserve_list[start_idx:end_idx]
        
        text = f"Candidate Reserve Pool" + "\n".join(page_data)
        if total_pages > 1: text += f"[Generic Template Field]"
        
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_reserve_keyboard(filter_type, page, total_pages, sort_type))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.error(f"Candidate Reserve Pool")
    finally:
        await callback.answer()

@admin_router.message(F.text == "Candidate Reserve Pool")
async def process_unreserve_btn(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    await state.set_state(AdminStates.waiting_for_unreserve)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel")]], resize_keyboard=True)
    await message.answer("[Generic Template Field]", reply_markup=kb)

@admin_router.message(F.text == "Candidate Reserve Pool")
async def process_broadcast_reserve_btn(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    await state.set_state(AdminStates.waiting_for_broadcast_target)
    
    kb = [[KeyboardButton(text="[Generic Template Field]")]]
    shift_buttons = [KeyboardButton(text=f"Shift {s['key']}") for s in SHIFT_SCHEDULE]
    for i in range(0, len(shift_buttons), 2):
        kb.append(shift_buttons[i:i+2])
    kb.append([KeyboardButton(text="[Generic Template Field]"), KeyboardButton(text="[Generic Template Field]")])
    kb.append([KeyboardButton(text="Cancel")])
    
    markup = ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    
    await message.answer("[Generic Template Field]", reply_markup=markup)

@admin_router.message(AdminStates.waiting_for_broadcast_target)
async def receive_broadcast_target(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_admin_keyboard(message.from_user.id))
        return

    target = message.text.strip()
    await state.update_data(broadcast_target=target)
    await state.set_state(AdminStates.waiting_for_broadcast_message)
    
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel")]], resize_keyboard=True)
    await message.answer(f"[Generic Template Field]", reply_markup=kb)

@admin_router.message(AdminStates.waiting_for_broadcast_message)
async def receive_broadcast_message(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
        
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_admin_keyboard(message.from_user.id))
        return

    data = await state.get_data()
    target = data.get("broadcast_target", "[Generic Template Field]")
    text_to_send = message.text

    await state.clear()
    kb = get_admin_keyboard(message.from_user.id)
    await message.answer("[Generic Template Field]", reply_markup=kb)

    reserve_users = await get_reserve_users()
    sent_count = 0
    
    from aiogram.exceptions import TelegramAPIError

    for row in reserve_users:
        try:
            uid = row['user_id']
            meta_str = row.get("metadata", "{}")
            if not meta_str: continue
            
            import json
            meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
            
            cand_shift = meta.get("desired_shift", "").lower()
            cand_exp = meta.get("has_experience", False)
            
            skip = False
            if target == "[Generic Template Field]" and cand_exp: skip = True
            elif target == "[Generic Template Field]" and not cand_exp: skip = True
            elif target.startswith("Shift "):
                target_key = target.replace("Shift ", "")
                if not match_shift_filter(cand_shift, target_key):
                    skip = True
            
            if skip: continue

            biz_id = meta.get("biz_id")
            if biz_id:
                await message.bot.send_message(uid, text_to_send, business_connection_id=biz_id)
            else:
                await message.bot.send_message(uid, text_to_send)
            
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.warning(f"An error occurred. Please try again later.")

    await message.answer(f"Changes saved successfully.")

@admin_router.message(AdminStates.waiting_for_unreserve)
async def receive_unreserve(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_admin_keyboard(message.from_user.id))
        return
        
    target = message.text.replace("@", "").strip()
    target_id = await resolve_target_user(target)

    kb = get_admin_keyboard(message.from_user.id)
    if not target_id:
        await state.clear()
        await message.answer(f"[Generic Template Field]", reply_markup=kb)
        return

    user_metadata[target_id]["is_timeout"] = False
    user_metadata[target_id]["followup_count"] = 0
    paused_users[target_id] = False
    await save_state(target_id)
    await state.clear()
    logger.info(f"Candidate Reserve Pool")
    await message.answer(f"Preferred Working Shift (00-08 / 08-16 / 16-00)", reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "[Generic Template Field]")
async def process_delete_btn(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(AdminStates.waiting_for_delete)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel")]], resize_keyboard=True)
    await message.answer("[Generic Template Field]", reply_markup=kb)

@admin_router.message(AdminStates.waiting_for_delete)
async def receive_delete(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_admin_keyboard(message.from_user.id))
        return
        
    target = message.text.replace("@", "").strip()
    target_id = await resolve_target_user(target)

    kb = get_admin_keyboard(message.from_user.id)
    if not target_id:
        await state.clear()
        await message.answer(f"[Generic Template Field]", reply_markup=kb)
        return

    try:
        await delete_user_from_db(target_id)
    except Exception as e:
        await state.clear()
        await message.answer(f"An error occurred. Please try again later.", reply_markup=kb)
        return

    user_history.pop(target_id, None); paused_users.pop(target_id, None)
    active_alerts.pop(target_id, None); user_metadata.pop(target_id, None)
    await state.clear()
    logger.info(f"[Generic Template Field]")
    await message.answer(f"Changes saved successfully.", reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "Preferred Working Shift (00-08 / 08-16 / 16-00)")
async def process_setshifts_btn(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    await state.set_state(AdminStates.waiting_for_shifts)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel")]], resize_keyboard=True)
    instruction = (
        "Preferred Working Shift (00-08 / 08-16 / 16-00)"
        "[Generic Template Field]"
        "[Generic Template Field]"
        "[Generic Template Field]"
        "Preferred Working Shift (00-08 / 08-16 / 16-00)"
        "Preferred Working Shift (00-08 / 08-16 / 16-00)"
    )
    await message.answer(instruction, reply_markup=kb, parse_mode="Markdown")

@admin_router.message(AdminStates.waiting_for_shifts)
async def receive_shifts(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("Preferred Working Shift (00-08 / 08-16 / 16-00)")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_admin_keyboard(message.from_user.id))
        return
        
    lines = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    parsed_data = []
    
    for line in lines:
        parts = [p.strip() for p in line.split('|')]
        if len(parts) >= 7:
            parsed_data.append({"name": parts[0], "top": parts[1], "total": parts[2], "time": parts[3], "exp": parts[4], "platform": parts[5], "deadline": parts[6]})
        elif len(parts) == 6:
            parsed_data.append({"name": parts[0], "top": parts[1], "total": parts[2], "time": parts[3], "exp": parts[4], "platform": parts[5], "deadline": "[Generic Template Field]"})
        elif len(parts) == 5:
            parsed_data.append({"name": parts[0], "top": parts[1], "total": parts[2], "time": parts[3], "exp": parts[4], "platform": "[Generic Template Field]", "deadline": "[Generic Template Field]"})
            
    kb = get_admin_keyboard(message.from_user.id)
    if not parsed_data:
        await message.answer("An error occurred. Please try again later.", reply_markup=kb)
        return
        
    new_shifts_list = actual_shifts_data + parsed_data
    await save_shifts_to_db(new_shifts_list)
    logger.info(f"Preferred Working Shift (00-08 / 08-16 / 16-00)")
    await state.clear()
    await message.answer(f"Preferred Working Shift (00-08 / 08-16 / 16-00)", reply_markup=kb)
    
    matched_count = 0
    notified_users = set()
    for shift in parsed_data:
        shift_time = shift['time'].lower()
        shift_exp = shift['exp'].lower() 
        
        filter_type = "all"
        for s in SHIFT_SCHEDULE:
            if s['key'] in shift_time or s['label'] in shift_time or s['label'].replace(' ', '') in shift_time:
                filter_type = s['key']
                break
        
        users_in_reserve = await get_reserve_users()
        for uid, meta, _ in users_in_reserve:
            if uid in notified_users: continue
            
            cand_shift = (meta.get("desired_shift") or "[Generic Template Field]").lower()
            cand_has_exp = meta.get("has_experience", False)
            
            if "[Generic Template Field]" in cand_shift or "Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)." in cand_shift: continue
            
            time_match = match_shift_filter(cand_shift, filter_type)
            if filter_type == "all" and (shift_time in cand_shift or cand_shift in shift_time): time_match = True
            
            exp_match = False
            if "[Generic Template Field]" in shift_exp and not cand_has_exp: exp_match = True
            elif "[Generic Template Field]" in shift_exp and cand_has_exp: exp_match = True
            elif "[Generic Template Field]" in shift_exp or "[Generic Template Field]" in shift_exp: exp_match = True
                
            if time_match and exp_match:
                text = f"Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
                try:
                    await ensure_user_loaded(uid)
                    
                    biz_id = meta.get("biz_id")
                    if biz_id:
                        await message.bot.send_message(uid, text, business_connection_id=biz_id)
                    else:
                        await message.bot.send_message(uid, text) 
                        
                    if uid not in user_history: user_history[uid] = []
                    user_history[uid].append({"role": "assistant", "content": text})
                    
                    user_metadata[uid]["is_timeout"] = False
                    user_metadata[uid]["followup_count"] = 0
                    paused_users[uid] = False
                    await save_state(uid)
                    notified_users.add(uid)
                    matched_count += 1
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"[Generic Template Field]")
                    
    if matched_count > 0:
        await message.answer(f"Preferred Working Shift (00-08 / 08-16 / 16-00)")

@admin_router.message((F.text == "/shifts") | (F.text == "Preferred Working Shift (00-08 / 08-16 / 16-00)"))
async def cmd_get_shifts(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    if not actual_shifts_data:
        await message.answer("Preferred Working Shift (00-08 / 08-16 / 16-00)")
        return
        
    text = "Preferred Working Shift (00-08 / 08-16 / 16-00)"
    for i, s in enumerate(actual_shifts_data):
        platform = s.get('platform', "[Generic Template Field]")
        text += f"[Generic Template Field]"
        text += f"Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
        text += f"[Generic Template Field]"
        text += f"[Generic Template Field]"
        text += f"[Generic Template Field]"
        text += "──────────────\n"
        
    markup = None
    if message.from_user.id in ALL_STAFF:
        buttons = []
        for i in range(len(actual_shifts_data)):
            buttons.append(InlineKeyboardButton(text=f"[Generic Template Field]", callback_data=f"delshift_{i}"))
        
        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        keyboard.append([InlineKeyboardButton(text="Preferred Working Shift (00-08 / 08-16 / 16-00)", callback_data="clear_all_shifts")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer(text, reply_markup=markup, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("delshift_"))
async def process_delshift(callback: CallbackQuery):
    if callback.from_user.id not in ALL_STAFF:
        await callback.answer("[Generic Template Field]", show_alert=True); return

    index = int(callback.data.split("_")[1])
    if 0 <= index < len(actual_shifts_data):
        deleted_shift = actual_shifts_data.pop(index)
        await save_shifts_to_db(actual_shifts_data)
        logger.info(f"Preferred Working Shift (00-08 / 08-16 / 16-00)")
        await callback.answer(f"Preferred Working Shift (00-08 / 08-16 / 16-00)")

        if not actual_shifts_data:
            await callback.message.edit_text("Preferred Working Shift (00-08 / 08-16 / 16-00)", reply_markup=None)
            return

        text = "Preferred Working Shift (00-08 / 08-16 / 16-00)"
        buttons = []
        for i, s in enumerate(actual_shifts_data):
            platform = s.get('platform', "[Generic Template Field]")
            text += f"[Generic Template Field]"
            text += f"Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift)."
            text += f"[Generic Template Field]"
            text += f"[Generic Template Field]"
            text += f"[Generic Template Field]"
            text += "──────────────\n"
            buttons.append(InlineKeyboardButton(text=f"[Generic Template Field]", callback_data=f"delshift_{i}"))

        keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
        keyboard.append([InlineKeyboardButton(text="Preferred Working Shift (00-08 / 08-16 / 16-00)", callback_data="clear_all_shifts")])
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await callback.answer("Preferred Working Shift (00-08 / 08-16 / 16-00)", show_alert=True)

@admin_router.callback_query(F.data == "clear_all_shifts")
async def process_clear_all_shifts(callback: CallbackQuery):
    if callback.from_user.id not in ALL_STAFF:
        await callback.answer("[Generic Template Field]", show_alert=True); return
        
    await save_shifts_to_db([])
    logger.info(f"Preferred Working Shift (00-08 / 08-16 / 16-00)")
    await callback.message.edit_text("Preferred Working Shift (00-08 / 08-16 / 16-00)", reply_markup=None, parse_mode="HTML")
    await callback.answer("Preferred Working Shift (00-08 / 08-16 / 16-00)")

@admin_router.message(F.text == "[Generic Template Field]")
async def process_toggle_bot_menu(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    await state.clear()
    btn_text = "[Generic Template Field]" if bot_config["is_active"] else "[Generic Template Field]"
    kb_list = [
        [KeyboardButton(text=btn_text)],
        [KeyboardButton(text="[Generic Template Field]")]
    ]
    if message.from_user.id in ADMIN_IDS:
        kb_list.append([KeyboardButton(text="[Generic Template Field]")])
        kb_list.append([KeyboardButton(text="Candidate Reserve Pool")])
    kb_list.append([KeyboardButton(text="Back to Menu")])
    
    kb = ReplyKeyboardMarkup(keyboard=kb_list, resize_keyboard=True)
    await message.answer("[Generic Template Field]", reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "Candidate Reserve Pool")
async def cmd_delete_reserve(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Candidate Reserve Pool", callback_data="confirm_delete_reserve")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_delete_reserve")]
    ])
    await message.answer("Candidate Reserve Pool", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "cancel_delete_reserve")
async def cancel_delete_reserve(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return await callback.answer("[Generic Template Field]", show_alert=True)
    await callback.message.edit_text("Candidate Reserve Pool")

@admin_router.callback_query(F.data == "confirm_delete_reserve")
async def confirm_delete_reserve(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return await callback.answer("[Generic Template Field]", show_alert=True)
    
    await callback.message.edit_text("Candidate Reserve Pool")
    from database import clear_reserve_users
    await clear_reserve_users()
    
    await callback.message.edit_text("Candidate Reserve Pool", parse_mode="HTML")

@admin_router.message(F.text == "[Generic Template Field]")
async def cmd_delete_all_users(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="[Generic Template Field]", callback_data="confirm_delete_all")],
        [InlineKeyboardButton(text="Cancel", callback_data="cancel_delete_all")]
    ])
    await message.answer("Please fill in your application details (Full Name, Age, Location, Experience, Preferred Shift).", reply_markup=kb, parse_mode="HTML")

@admin_router.callback_query(F.data == "cancel_delete_all")
async def cancel_delete_all(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return await callback.answer("[Generic Template Field]", show_alert=True)
    await callback.message.edit_text("Cancel")

@admin_router.callback_query(F.data == "confirm_delete_all")
async def confirm_delete_all(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return await callback.answer("[Generic Template Field]", show_alert=True)
    await callback.message.edit_text("Record deleted.")
    try:
        await delete_all_users_from_db()
        logger.warning(f"Back to Menu")
        await callback.message.edit_text("Changes saved successfully.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"An error occurred. Please try again later.")
        await callback.message.edit_text(f"An error occurred. Please try again later.")

@admin_router.message(F.text == "Back to Menu")
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ALL_STAFF: return
    kb = get_admin_keyboard(message.from_user.id)
    await message.answer("[Generic Template Field]", reply_markup=kb)

@admin_router.message(F.text.in_(["[Generic Template Field]", "[Generic Template Field]"]))
async def toggle_global_bot(message: Message):
    if message.from_user.id not in ALL_STAFF: return
    new_state = message.text == "[Generic Template Field]"
    await set_bot_active(new_state)
    logger.info(f"[Generic Template Field]")
    
    btn_text = "[Generic Template Field]" if bot_config["is_active"] else "[Generic Template Field]"
    kb_list = [
        [KeyboardButton(text=btn_text)],
        [KeyboardButton(text="[Generic Template Field]")]
    ]
    if message.from_user.id in ADMIN_IDS:
        kb_list.append([KeyboardButton(text="[Generic Template Field]")])
    kb_list.append([KeyboardButton(text="Back to Menu")])
    
    kb = ReplyKeyboardMarkup(keyboard=kb_list, resize_keyboard=True)
    
    status_text = "[Generic Template Field]" if new_state else "[Generic Template Field]"
    await message.answer(status_text, reply_markup=kb, parse_mode="HTML")

@admin_router.message(F.text == "[Generic Template Field]")
async def process_toggle_user_btn(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    await state.set_state(AdminStates.waiting_for_toggle_bot)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel")]], resize_keyboard=True)
    await message.answer("[Generic Template Field]", reply_markup=kb)

@admin_router.message(AdminStates.waiting_for_toggle_bot)
async def receive_toggle_bot(message: Message, state: FSMContext):
    if message.from_user.id not in ALL_STAFF: return
    if not message.text:
        await message.answer("[Generic Template Field]")
        return
    if message.text in ["Cancel", "🔙 Main Menu"]:
        await state.clear()
        await message.answer("Cancel", reply_markup=get_admin_keyboard(message.from_user.id))
        return
        
    target = message.text.replace("@", "").strip()
    target_id = await resolve_target_user(target)

    kb = get_admin_keyboard(message.from_user.id)
    if not target_id:
        await state.clear()
        await message.answer(f"[Generic Template Field]", reply_markup=kb)
        return

    current_status = paused_users.get(target_id, False)
    paused_users[target_id] = not current_status
    await save_state(target_id)
    
    await state.clear()
    if not current_status:
        logger.info(f"[Generic Template Field]")
        await message.answer(f"[Generic Template Field]", reply_markup=kb, parse_mode="HTML")
    else:
        logger.info(f"[Generic Template Field]")
        await resolve_alerts(message.bot, target_id, "[Generic Template Field]")
        await message.answer(f"[Generic Template Field]", reply_markup=kb, parse_mode="HTML")