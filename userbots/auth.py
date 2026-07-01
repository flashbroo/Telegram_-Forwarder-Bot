
import db
import os
import re
import traceback
from telethon.errors import (
    ApiIdInvalidError,
    ApiIdPublishedFloodError,
    AuthRestartError,
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError
)
from userbots.manager import create_client, get_client, drop_client, get_session_paths
from config import USERBOT_API_HASH, USERBOT_API_ID

login_states = {}
active_logins = {}


async def _cleanup_login_state(user_id: int, state=None, keep_session_file: bool = False):
    saved_state = login_states.pop(user_id, None)
    state = state or saved_state
    if not state:
        return

    phone = state.get("phone")
    client = state.get("client")

    if phone:
        active_logins.pop(phone, None)

    if client:
        try:
            await client.disconnect()
        except:
            pass

    await drop_client(user_id)

    if keep_session_file:
        return

    session_file, session_journal = get_session_paths(user_id)

    try:
        if os.path.exists(session_file):
            os.remove(session_file)
    except:
        pass

    try:
        if os.path.exists(session_journal):
            os.remove(session_journal)
    except:
        pass


# -----------------------------
# START LOGIN
# -----------------------------
async def start_login(user_id: int, phone: str):
    phone = phone.strip().replace(" ", "")

    print(f"[DEBUG PHONE] {phone}")

    if not USERBOT_API_ID or not USERBOT_API_HASH:
        return "USERBOT_CONFIG_MISSING"

    # 🔥 CLEAN OLD STATE
    old_state = login_states.pop(user_id, None)
    if old_state:
        await _cleanup_login_state(user_id, old_state)

    await drop_client(user_id)

    # 🔥 DELETE SESSION FILES (SAFE)
    session_file, session_journal = get_session_paths(user_id)

    try:
        if os.path.exists(session_file):
            os.remove(session_file)
    except:
        pass

    try:
        if os.path.exists(session_journal):
            os.remove(session_journal)
    except:
        pass

    # 🔥 FIXED ACTIVE LOGIN CLEANUP
    if phone in active_logins and active_logins[phone] == user_id:
        active_logins.pop(phone, None)

    # 🔒 prevent multi-user use
    owner = db.get_phone_owner(phone)
    if owner and owner != user_id:
        return "PHONE_ALREADY_REGISTERED"

    if phone in active_logins and active_logins[phone] != user_id:
        return "PHONE_IN_USE"

    # 🔥 CREATE FRESH CLIENT
    client = create_client(user_id)
    try:
        await client.connect()
    except Exception:
        traceback.print_exc()
        await drop_client(user_id)
        return "CONNECT_ERROR"

    try:
        print("[DEBUG] Sending OTP...")
        result = await client.send_code_request(phone)
        print("[DEBUG] OTP sent successfully")

    except FloodWaitError as e:
        print(f"[ERROR] Flood wait: {e}")
        await drop_client(user_id)
        return "FLOOD"

    except PhoneNumberFloodError:
        await drop_client(user_id)
        return "PHONE_FLOOD"

    except PhoneNumberInvalidError:
        await drop_client(user_id)
        return "INVALID_PHONE"

    except PhoneNumberBannedError:
        await drop_client(user_id)
        return "PHONE_BANNED"

    except ApiIdInvalidError:
        await drop_client(user_id)
        return "API_ID_INVALID"

    except ApiIdPublishedFloodError:
        await drop_client(user_id)
        return "API_ID_PUBLISHED_FLOOD"

    except AuthRestartError:
        await drop_client(user_id)
        return "AUTH_RESTART"

    except Exception as e:
        print("====== FULL ERROR ======")
        traceback.print_exc()
        print("========================")
        await drop_client(user_id)
        return "ERROR"

    login_states[user_id] = {
        "phone": phone,
        "client": client,
        "phone_code_hash": result.phone_code_hash,
        "attempts": 0
    }

    active_logins[phone] = user_id

    return "OTP_SENT"


# -----------------------------
# VERIFY OTP
# -----------------------------
async def verify_otp(user_id: int, user_input: str):
    state = login_states.get(user_id)

    if not state:
        return "ERROR"

    client = state["client"]
    phone = state["phone"]

    state["attempts"] += 1

    if state["attempts"] > 5:
        active_logins.pop(phone, None)
        login_states.pop(user_id, None)

        try:
            await client.disconnect()
        except:
            pass

        await _cleanup_login_state(user_id, state, keep_session_file=True)
        return "TOO_MANY_ATTEMPTS"

    user_input = user_input.strip().replace(" ", "").replace("-", "")

    if len(user_input) < 3:
        return "INVALID_FORMAT"

    match = re.fullmatch(r"(\d{3,8})([A-Za-z]*)", user_input)
    if not match:
        return "INVALID_FORMAT"

    otp = match.group(1)
    if not otp:
        return "INVALID_FORMAT"

    try:
        await client.sign_in(
            phone=phone,
            code=otp,
            phone_code_hash=state["phone_code_hash"]
        )

        db.assign_phone(phone, user_id)

        active_logins.pop(phone, None)
        login_states.pop(user_id, None)

        return "LOGGED_IN"

    except SessionPasswordNeededError:
        state["password_required"] = True
        return "2FA_REQUIRED"

    except Exception as e:
        print("====== LOGIN ERROR ======")
        traceback.print_exc()
        print("=========================")
        await _cleanup_login_state(user_id, state, keep_session_file=True)
        return "INVALID_OTP"


async def verify_2fa_password(user_id: int, password: str):
    state = login_states.get(user_id)

    if not state:
        return "ERROR"

    client = state["client"]
    phone = state["phone"]
    state["attempts"] += 1

    if state["attempts"] > 8:
        await _cleanup_login_state(user_id, state, keep_session_file=True)
        return "TOO_MANY_ATTEMPTS"

    try:
        await client.sign_in(password=password)
        db.assign_phone(phone, user_id)
        await _cleanup_login_state(user_id, state, keep_session_file=True)
        return "LOGGED_IN"
    except Exception:
        print("====== 2FA ERROR ======")
        traceback.print_exc()
        print("=======================")
        return "INVALID_PASSWORD"


# -----------------------------
# CHECK LOGIN (LEGACY)
# -----------------------------
def is_logged_in(user_id: int):
    client = get_client(user_id)
    if not client:
        return False

    try:
        return client.is_connected() and client.is_user_authorized()
    except:
        return False


# -----------------------------
# LOGOUT
# -----------------------------
async def logout(user_id: int):
    client = get_client(user_id)

    if client:
        try:
            await client.log_out()
        except:
            pass

        try:
            await client.disconnect()
        except:
            pass

    session_file, session_journal = get_session_paths(user_id)

    try:
        if os.path.exists(session_file):
            os.remove(session_file)
    except:
        pass

    try:
        if os.path.exists(session_journal):
            os.remove(session_journal)
    except:
        pass

    db.remove_phone(user_id)
    await drop_client(user_id)

    print(f"[DEBUG] User {user_id} logged out + session deleted")
