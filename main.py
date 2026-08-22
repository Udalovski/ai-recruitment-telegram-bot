import logging
import asyncio
from logging.handlers import RotatingFileHandler
import os
import re
import base64
import io
import gspread
import datetime
from zoneinfo import ZoneInfo
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, BotCommand, LinkPreviewOptions
)
from anthropic import AsyncAnthropic
import random

from database import (
    init_db, save_state, user_history, paused_users, active_alerts, user_metadata, 
    custom_vacancies, ensure_user_loaded, get_all_states, muted_admins, bot_config,
    SHIFT_SCHEDULE
)
from utils import (
    ALL_STAFF, resolve_alerts, export_candidate_to_sheet, get_shifts_prompt_text, check_google_form_score, check_all_tests
)
from admin_handlers import admin_router

log_handler = RotatingFileHandler("hrbot.log", maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    force=True,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[log_handler, logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

try:
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()
    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    dp.include_router(admin_router)
except Exception as e:
    logger.critical(f"Initialization Error (check .env credentials): {e}")
    raise

gc = None
try:
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    gc = gspread.authorize(creds)
    logger.info("Successfully connected to Google Sheets!")
except Exception as e:
    logger.warning(f"Could not connect to Google Sheets: {e}")

KNOWLEDGE_BASE_TEXT = """Recruitment Guidelines & General Candidate Evaluation Rubric:
- Evaluate candidate communication skills, responsiveness, and professional demeanor.
- Ensure the candidate meets the minimum availability requirements (at least 5 shifts per week, 8 hours per shift).
- Confirm hardware and internet stability for remote positions (PC/Laptop, stable broadband connection).
- Check language proficiency (fluent written and verbal English).
- Verify candidate identity and document submissions when requested.
- If candidate meets all qualifications, guide them to the onboarding materials and practical test assignment.
"""
user_buffers = {} 
user_tasks = {}

CONDITIONS_TEXT = """👋 <b>Welcome to our Career & Recruitment Portal!</b>

We are actively hiring for remote positions with flexible hours and competitive compensation.

📌 <b>What We Offer:</b>
• 🌐 Fully remote work with flexible shift choices (Morning / Day / Night).
• 💰 Competitive compensation with performance bonuses and timely payouts.
• 📚 Comprehensive onboarding and structured training from day one.
• 🚀 Supportive international team and long-term career growth.

🔗 <b>Job Overview & Requirements:</b>
https://example.com/job-overview

Please reply with your preferred opening or questions to begin!"""

QUESTIONNAIRE_TEXT = f"""📋 <b>Candidate Application Questionnaire</b>

Please copy and fill out the details below in a single reply message:

1. <b>Full Name:</b>

2. <b>Age:</b>

3. <b>Location (City / Country & Timezone):</b>

4. <b>Relevant Work Experience:</b>

5. <b>Languages Spoken & Proficiency Levels:</b>
• English:
• Other languages:

6. <b>Preferred Working Shift:</b>
• 🌅 Morning (00:00 - 08:00)
• ☀️ Day (08:00 - 16:00)
• 🌆 Evening (16:00 - 00:00)

7. <b>Hardware & Technical Setup:</b>
• Do you have a personal computer / laptop?
• Stable high-speed internet connection?

8. <b>How many shifts per week are you available to work?</b>

9. <b>When are you ready to start?</b>

10. <b>Why are you interested in joining our team?</b>"""

VERIFICATION_TEXT = """🔒 <b>Identity & Background Verification</b>

To maintain security and compliance for remote positions, please provide standard identity verification:

📸 <b>Instructions:</b>
1. Prepare a clear photo of your government-issued ID (Passport, National ID, or Driver's License).
2. Take a clear selfie holding your ID next to your face.
3. Ensure all details and text are legible.

Send the images directly into this chat when ready."""

TRAINING_TEXT = """📚 <b>Onboarding & Test Assignment</b>

Congratulations on passing the initial screening! Below are your study materials and practical task:

📖 <b>Materials:</b>
1. General Guidelines: https://example.com/onboarding-guide
2. Workflow Standards: https://example.com/training-materials

⏱️ <b>Assignment Details:</b>
• Review the materials thoroughly.
• Complete the practical assignment within 24 hours.
• Submit your answers in this chat for evaluation."""

INTERVIEW_TEXT = """🎯 <b>Situational & Technical Assessment</b>

Please answer the following situational questions:

1. <b>Communication Scenario:</b> How do you handle high-priority, urgent requests under tight deadlines?
2. <b>Conflict Resolution:</b> How do you resolve misunderstandings with team members or clients?
3. <b>Time Management:</b> What strategies do you use to stay focused and productive during remote shifts?

Reply with your answers directly in this chat."""

GOODBYE_TEXT = """🎉 <b>Welcome to the Team!</b>

Your application and test results have been approved.

🔗 <b>Next Steps:</b>
• Team Portal: https://example.com/team-portal
• Announcements Channel: https://t.me/example_channel

Your manager will contact you shortly regarding credentials and shift onboarding."""

NOTION_PRACTICE_ETALON = """Evaluation Reference Standard:
1. Communication Tone: Professional, clear, polite, and grammatically accurate.
2. Responsiveness: Able to follow complex instructions accurately and meet deadlines.
3. Problem Solving: Demonstrates critical thinking, initiative, and policy adherence.
4. Shift Commitment: Clear agreement to working hours and attendance expectations.
"""

@dp.business_message()
@dp.message(~F.from_user.id.in_(ALL_STAFF))
async def handle_business_message(message: Message):
    user_id = message.from_user.id
    await ensure_user_loaded(user_id)

    if not bot_config.get("is_active", True) and user_id not in ALL_STAFF:
        await message.answer("The recruitment portal is currently undergoing scheduled maintenance. Please check back shortly.", parse_mode="HTML")
        return

    if paused_users.get(user_id, False):
        return

    if user_id not in user_buffers: 
        user_buffers[user_id] = []
    
    text_part = message.text or message.caption or ""
    if text_part:
        user_buffers[user_id].append(text_part)

    if message.photo:
        photo = message.photo[-1]
        try:
            file_info = await bot.get_file(photo.file_id)
            photo_bytes = await bot.download_file(file_info.file_path)
            base64_image = base64.b64encode(photo_bytes.read()).decode("utf-8")
            user_buffers[user_id].append({"type": "image", "data": base64_image})
            logger.info(f"Candidate {user_id} sent photo: {photo.file_id}")
        except Exception as e:
            logger.error(f"Error processing photo: {e}")

    if user_id in user_tasks and not user_tasks[user_id].done():
        user_tasks[user_id].cancel()

    wait_time = 3.0 if user_id in ALL_STAFF else 60.0
    user_tasks[user_id] = asyncio.create_task(process_buffered_messages(user_id, message, wait_time))

async def process_buffered_messages(user_id, message: Message, wait_time=60):
    await asyncio.sleep(wait_time)
    await _process_internal(user_id, message, wait_time)

async def _process_internal(user_id, message: Message, wait_time=60):
    logger.info(f"Starting debounce timer ({wait_time}s) for candidate {user_id}")
    
    if user_id not in user_buffers or not user_buffers[user_id]:
        return
    
    buffers = user_buffers.pop(user_id, [])
    
    combined_content = []
    text_pieces = []
    
    for item in buffers:
        if isinstance(item, str):
            text_pieces.append(item)
        elif isinstance(item, dict) and item.get("type") == "image":
            combined_content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": item["data"]
                }
            })

    if text_pieces:
        combined_content.append({
            "type": "text",
            "text": "\n".join(text_pieces)
        })

    user_text = "\n".join(text_pieces)
    if user_id not in user_history: 
        user_history[user_id] = []
        
    user_history[user_id].append({
        "role": "user",
        "content": combined_content if len(combined_content) > 1 or (combined_content and combined_content[0].get("type") == "image") else user_text
    })
    
    if len(user_history[user_id]) > 12:
        trimmed = user_history[user_id][-12:]
        if trimmed[0]["role"] == "assistant": 
            trimmed = trimmed[1:]
        user_history[user_id] = trimmed
    
    if message.business_connection_id:
        try: 
            await bot.read_business_message(
                business_connection_id=message.business_connection_id, 
                chat_id=message.chat.id, 
                message_id=message.message_id
            )
        except Exception: 
            pass

    await save_state(user_id)
    
    if user_id not in user_metadata: 
        user_metadata[user_id] = {
            "stage": "1. Introduction", 
            "vacancy": "Not selected", 
            "username": message.chat.username or "", 
            "conditions_sent": False, 
            "form_sent": False, 
            "verify_sent": False
        }
    else: 
        user_metadata[user_id]["username"] = message.chat.username or ""
        if "conditions_sent" not in user_metadata[user_id]: user_metadata[user_id]["conditions_sent"] = False
        if "form_sent" not in user_metadata[user_id]: user_metadata[user_id]["form_sent"] = False
        if "verify_sent" not in user_metadata[user_id]: user_metadata[user_id]["verify_sent"] = False
            
    meta = user_metadata[user_id]
    
    if message.business_connection_id:
        user_metadata[user_id]["biz_id"] = message.business_connection_id
    
    current_vac = meta.get("vacancy", "Not selected")
    is_custom_vac = False
    
    actual_vac_key = next((k for k in custom_vacancies if k.lower() == current_vac.lower()), None)
    
    if actual_vac_key:
        is_custom_vac = True
        actual_cond_text = custom_vacancies[actual_vac_key]["conditions"]
        actual_form_text = custom_vacancies[actual_vac_key]["form"]
        actual_kb_text = custom_vacancies[actual_vac_key].get("kb", "Knowledge base for this vacancy is currently empty.") 
    else:
        actual_cond_text = bot_config.get("chatter_conditions") or CONDITIONS_TEXT
        actual_form_text = bot_config.get("chatter_form") or QUESTIONNAIRE_TEXT
        actual_kb_text = bot_config.get("chatter_kb") or KNOWLEDGE_BASE_TEXT

    conditions_instruction = "CONDITIONS HAVE ALREADY BEEN SENT. DO NOT USE [SEND_CONDITIONS] AGAIN!" if meta.get("conditions_sent") else "As soon as the candidate confirms their chosen vacancy, your reply must consist STRICTLY of one tag: [SEND_CONDITIONS]. Do not write anything else."
    form_instruction = "APPLICATION FORM HAS ALREADY BEEN SENT. DO NOT USE [SEND_FORM] AGAIN!" if meta.get("form_sent") else "As soon as the candidate agrees to the working conditions, your reply must consist STRICTLY of one tag: [SEND_FORM]. Do not write anything else."
    verify_instruction = "VERIFICATION REQUEST HAS ALREADY BEEN SENT. DO NOT USE [SEND_VERIFY] AGAIN!" if meta.get("verify_sent") else "If the application form is complete and suitable, your reply must consist STRICTLY of one tag: [SEND_VERIFY]. Do not write anything else."

    system_prompt = f"""You are an automated, professional HR recruiter for a fast-growing remote team.
Your objective is to conduct candidate screening, evaluate questionnaires, verify requirements, and guide candidates through onboarding.

Behavioral Guidelines:
1. Maintain a polite, welcoming, structured, and professional tone.
2. Carefully check candidate answers against position requirements.
3. If information is missing or incomplete, politely ask clarifying questions.
4. Follow the step-by-step recruitment pipeline:
   - Step 1: Vacancy selection & Introduction -> {conditions_instruction}
   - Step 2: Conditions confirmation -> {form_instruction}
   - Step 3: Application evaluation -> {verify_instruction}
   - Step 4: Verification & practical onboarding delivery -> [SEND_TRAINING]
   - Step 5: Final situational interview assessment -> [SEND_INTERVIEW]
   - Step 6: Approval and team onboarding -> [SEND_GOODBYE]

Control Tags:
- [SEND_CONDITIONS] - Send job conditions text.
- [SEND_FORM] - Send questionnaire form.
- [SEND_VERIFY] - Send verification instructions.
- [SEND_TRAINING] - Send training assignment.
- [SEND_INTERVIEW] - Send situational questions.
- [SEND_GOODBYE] - Send final approval message.
- [ALERT: <reason>] - Trigger notification for HR staff.
- [PAUSE] - Pause automated responses.
- [QUALIFIED] - Mark candidate as qualified.
- [RESERVE] - Move candidate to talent reserve.
- [SCHEDULE_SHIFT: 00-08 | 08-16 | 16-00] - Record chosen shift.
- [CHECK_ALL_TESTS] - Verify Google Sheets test submissions.

Knowledge Base Reference:
{actual_kb_text}

Candidate Metadata:
- Vacancy: {current_vac}
- Current Stage: {meta.get("stage", "1. Introduction")}
- Conditions Sent: {meta.get("conditions_sent")}
- Form Sent: {meta.get("form_sent")}
- Verification Sent: {meta.get("verify_sent")}
"""

    try:
        logger.info(f"Sending request to Claude API for candidate {user_id}...")
        response = await client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1500,
            system=system_prompt,
            messages=user_history[user_id][-12:]
        )
        reply_text = response.content[0].text
        logger.info(f"Response received from Claude API for candidate {user_id}")

        if "[SEND_CONDITIONS]" in reply_text:
            meta["conditions_sent"] = True
            meta["stage"] = "2. Conditions"
            await message.answer(actual_cond_text, parse_mode="HTML")
            reply_text = re.sub(r'\[SEND_CONDITIONS\]', '', reply_text).strip()

        if "[SEND_FORM]" in reply_text:
            meta["form_sent"] = True
            meta["stage"] = "3. Questionnaire"
            await message.answer(actual_form_text, parse_mode="HTML")
            reply_text = re.sub(r'\[SEND_FORM\]', '', reply_text).strip()

        if "[SEND_VERIFY]" in reply_text:
            meta["verify_sent"] = True
            meta["stage"] = "4. Verification"
            await message.answer(VERIFICATION_TEXT, parse_mode="HTML")
            reply_text = re.sub(r'\[SEND_VERIFY\]', '', reply_text).strip()

        if "[SEND_TRAINING]" in reply_text:
            meta["stage"] = "5. Training"
            await message.answer(TRAINING_TEXT, parse_mode="HTML")
            reply_text = re.sub(r'\[SEND_TRAINING\]', '', reply_text).strip()

        if "[SEND_INTERVIEW]" in reply_text:
            meta["stage"] = "6. Interview"
            await message.answer(INTERVIEW_TEXT, parse_mode="HTML")
            reply_text = re.sub(r'\[SEND_INTERVIEW\]', '', reply_text).strip()

        if "[SEND_GOODBYE]" in reply_text:
            meta["stage"] = "7. Completed"
            meta["status"] = "Qualified"
            export_candidate_to_sheet(gc, user_id, message.from_user)
            await message.answer(GOODBYE_TEXT, parse_mode="HTML")
            reply_text = re.sub(r'\[SEND_GOODBYE\]', '', reply_text).strip()

        if "[ALERT:" in reply_text:
            match = re.search(r'\[ALERT:\s*(.*?)\]', reply_text)
            alert_reason = match.group(1) if match else "Manual review requested"
            for admin_id in ALL_STAFF:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🚨 <b>Candidate Notification:</b> <a href=\"tg://user?id={user_id}\">{message.from_user.full_name}</a> (ID: <code>{user_id}</code>)\nReason: <code>{alert_reason}</code>",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass
            reply_text = re.sub(r'\[ALERT:.*?\]', '', reply_text).strip()

        if "[PAUSE]" in reply_text:
            paused_users[user_id] = True
            reply_text = re.sub(r'\[PAUSE\]', '', reply_text).strip()

        if "[QUALIFIED]" in reply_text:
            meta["status"] = "Qualified"
            export_candidate_to_sheet(gc, user_id, message.from_user)
            reply_text = re.sub(r'\[QUALIFIED\]', '', reply_text).strip()

        if "[RESERVE]" in reply_text:
            meta["in_reserve"] = True
            reply_text = re.sub(r'\[RESERVE\]', '', reply_text).strip()

        if "[SCHEDULE_SHIFT:" in reply_text:
            shift_match = re.search(r'\[SCHEDULE_SHIFT:\s*(.*?)\]', reply_text)
            if shift_match:
                meta["shift"] = shift_match.group(1).strip()
            reply_text = re.sub(r'\[SCHEDULE_SHIFT:.*?\]', '', reply_text).strip()

        if "[CHECK_ALL_TESTS]" in reply_text:
            test_results = check_all_tests(gc, message.from_user.full_name)
            if test_results.get("passed"):
                reply_text += f"\n\n✅ <b>Test Verification:</b> Passed with score {test_results.get('score')}%."
            reply_text = re.sub(r'\[CHECK_ALL_TESTS\]', '', reply_text).strip()

        if reply_text:
            user_history[user_id].append({"role": "assistant", "content": reply_text})
            if len(reply_text) > 4000:
                for chunk in [reply_text[i:i+4000] for i in range(0, len(reply_text), 4000)]:
                    await message.answer(chunk, parse_mode="HTML")
            else:
                await message.answer(reply_text, parse_mode="HTML")

        meta["last_active"] = datetime.datetime.now().isoformat()
        await save_state(user_id)

    except Exception as e:
        logger.error(f"Error calling Claude API: {e}")
        await message.answer("Thank you for your reply! Our HR team will review your application and get back to you shortly.", parse_mode="HTML")

@dp.callback_query(F.data.startswith("unpause_"))
async def process_unpause(callback: CallbackQuery):
    user_id = int(callback.data.replace("unpause_", ""))
    paused_users[user_id] = False
    await save_state(user_id)
    await callback.answer(f"Bot resumed for candidate {user_id}.", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)

def _create_fake_message(target_user_id: int):
    class FakeChat:
        id = target_user_id
        username = user_metadata.get(target_user_id, {}).get("username", "")
        full_name = f"Candidate {target_user_id}"
    class FakeUser:
        id = target_user_id
        full_name = f"Candidate {target_user_id}"
        username = user_metadata.get(target_user_id, {}).get("username", "")
    class FakeMessage:
        chat = FakeChat()
        from_user = FakeUser()
        message_id = 0
        business_connection_id = user_metadata.get(target_user_id, {}).get("biz_id", None)
        text = ""
        caption = ""
        photo = None
        async def answer(self, text, **kwargs):
            return await bot.send_message(chat_id=target_user_id, text=text, **kwargs)
    return FakeMessage()

async def _resume_bot_with_system_msg(target_user_id: int, sys_msg: str, callback: CallbackQuery, log_text: str, resolve_text: str):
    paused_users[target_user_id] = False
    if target_user_id not in user_history: 
        user_history[target_user_id] = []
    user_history[target_user_id].append({"role": "user", "content": sys_msg})
    await save_state(target_user_id)
    await resolve_alerts(bot, target_user_id, resolve_text)
    fake_msg = _create_fake_message(target_user_id)
    user_buffers[target_user_id] = [sys_msg]
    asyncio.create_task(process_buffered_messages(target_user_id, fake_msg, wait_time=0))
    logger.info(f"{log_text} for candidate {target_user_id}")
    await callback.answer("Bot resumed with updated instructions.", show_alert=True)

@dp.callback_query(F.data.regexp(r'^hr_(vfy|bls|exp|ntn|interview)_(yes|no|yc|nc|back)_\d+$'))
async def process_hr_action(callback: CallbackQuery):
    data = callback.data.split("_")
    action_type = data[1]
    decision = data[2]
    user_id = int(data[3])
    
    await ensure_user_loaded(user_id)
    admin_name = callback.from_user.full_name
    
    if decision in ["yes", "yc"]:
        user_metadata[user_id]["status"] = f"Approved ({action_type})"
        resolve_text = f"✅ Approved by HR ({admin_name})"
        log_text = f"HR Action: Approved {action_type}"
        sys_msg = f"[SYSTEM: HR Manager approved candidate {action_type}. Proceed to the next stage.]"
        await _resume_bot_with_system_msg(user_id, sys_msg, callback, log_text, resolve_text)
    elif decision in ["no", "nc"]:
        user_metadata[user_id]["status"] = f"Rejected ({action_type})"
        resolve_text = f"❌ Rejected by HR ({admin_name})"
        log_text = f"HR Action: Rejected {action_type}"
        sys_msg = f"[SYSTEM: HR Manager rejected candidate {action_type}. Politely notify candidate.]"
        await _resume_bot_with_system_msg(user_id, sys_msg, callback, log_text, resolve_text)
    elif decision == "back":
        await callback.answer("Cancelled action.", show_alert=False)

    await save_state(user_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

async def background_followup_task():
    while True:
        try:
            await asyncio.sleep(1800)  # Every 30 minutes
            now = datetime.datetime.now()
            for user_id, meta in list(user_metadata.items()):
                if paused_users.get(user_id, False) or meta.get("status") in ["Qualified", "Rejected", "Archived"]:
                    continue
                last_active_str = meta.get("last_active")
                if not last_active_str:
                    continue
                last_active = datetime.datetime.fromisoformat(last_active_str)
                hours_inactive = (now - last_active).total_seconds() / 3600.0
                
                stage = meta.get("followup_stage", 0)
                if hours_inactive >= 24.0 and stage == 0:
                    meta["followup_stage"] = 1
                    await send_followup(user_id, attempt_num=1)
                elif hours_inactive >= 48.0 and stage == 1:
                    meta["followup_stage"] = 2
                    await send_followup(user_id, attempt_num=2)
                elif hours_inactive >= 72.0 and stage == 2:
                    meta["followup_stage"] = 3
                    meta["in_reserve"] = True
                    await send_followup(user_id, attempt_num=3)
                await save_state(user_id)
        except Exception as e:
            logger.error(f"Error in background follow-up worker: {e}")

async def send_followup(user_id: int, attempt_num: int):
    prompts = {
        1: "👋 Hello! Just checking in to see if you had any questions regarding the questionnaire?",
        2: "📌 Friendly reminder: we are reviewing candidate submissions for this week's onboarding batch. Would you like to proceed with your application?",
        3: "⏳ Your application has been moved to our talent reserve pool. Whenever you are ready to resume, simply reply to this message!"
    }
    msg = prompts.get(attempt_num, "Hello! Let us know if you would like to continue your application.")
    try:
        await bot.send_message(user_id, msg, parse_mode="HTML")
        logger.info(f"Sent follow-up #{attempt_num} to candidate {user_id}")
    except Exception as e:
        logger.debug(f"Could not send follow-up to candidate {user_id}: {e}")

async def background_daily_hr_reminder_task():
    while True:
        try:
            now = datetime.datetime.now()
            target_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if now >= target_time:
                target_time += datetime.timedelta(days=1)
            wait_seconds = (target_time - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            
            active_dialogues = len(user_history)
            reserve_count = len([u for u in user_metadata.values() if u.get("in_reserve", False)])
            qualified_count = len([u for u in user_metadata.values() if u.get("status") == "Qualified"])
            
            digest = (
                "🌅 <b>Daily HR Recruitment Summary:</b>\n\n"
                f"• Active Candidate Dialogues: <code>{active_dialogues}</code>\n"
                f"• Qualified Candidates: <code>{qualified_count}</code>\n"
                f"• Candidates in Reserve: <code>{reserve_count}</code>\n\n"
                "Review pending submissions via the /admin dashboard."
            )
            for staff_id in ALL_STAFF:
                if staff_id not in muted_admins:
                    try:
                        await bot.send_message(staff_id, digest, parse_mode="HTML")
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Error in daily HR reminder task: {e}")

async def background_memory_cleanup_task():
    while True:
        try:
            await asyncio.sleep(21600)  # Every 6 hours
            logger.info("Executing periodic memory cache optimization...")
            cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
            for uid, meta in list(user_metadata.items()):
                last_active_str = meta.get("last_active")
                if last_active_str:
                    last_active = datetime.datetime.fromisoformat(last_active_str)
                    if last_active < cutoff and uid not in paused_users:
                        user_history.pop(uid, None)
        except Exception as e:
            logger.error(f"Error in memory cleanup worker: {e}")

async def main():
    logger.info("Starting AI Recruitment Telegram Bot...")
    await init_db()
    
    asyncio.create_task(background_followup_task())
    asyncio.create_task(background_daily_hr_reminder_task())
    asyncio.create_task(background_memory_cleanup_task())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
