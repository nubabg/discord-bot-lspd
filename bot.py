import os
import discord
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import time
import pytz
import json

# Времева зона
sofia_tz = pytz.timezone("Europe/Sofia")

# ✅ Взимане на токена от средата (Railway Variables)
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌ Грешка: DISCORD_TOKEN не е намерен!")
    exit(1)

# Създаване на intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)

# Връзка с Google Sheets
SHEET_NAME = "LSPD BOT"
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

credentials_content = os.getenv("CREDENTIALS_JSON")
if not credentials_content:
    print("❌ Грешка: CREDENTIALS_JSON не е зададен!")
    exit(1)

creds_dict = json.loads(credentials_content)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# ------------------ Помощни функции за структурата ------------------

def ensure_structure(shifts_sheet, leaves_sheet):
    """Уверява се, че имаме нужните колони/заглавия и добавя UserID колона при нужда."""
    # Shifts
    values = shifts_sheet.get_all_values()
    if not values:
        shifts_sheet.append_row(["Потребител", "Начало", "Край", "Изработено време", "UserID"])
    else:
        headers = values[0]
        # Ако няма колона UserID -> добавяме я в колона E
        if "UserID" not in headers:
            # Пишем "UserID" на E1
            shifts_sheet.update("E1", [["UserID"]])
            # Попълваме празни стойности за останалите редове (за да съществува колоната)
            if len(values) > 1:
                empty_col = [[""] for _ in range(len(values) - 1)]
                shifts_sheet.update(f"E2:E{len(values)}", empty_col)
        # Ако има по-малко от 4 основни колони, поправяме заглавията
        base_headers = ["Потребител", "Начало", "Край", "Изработено време"]
        need_update = False
        for i, name in enumerate(base_headers):
            if i >= len(headers) or headers[i] != name:
                need_update = True
        if need_update:
            # Синхронизираме първите 4 заглавия (не трия нищо, само фиксирам текстовете)
            for i, name in enumerate(base_headers, start=1):
                shifts_sheet.update_cell(1, i, name)

    # Leaves
    values_l = leaves_sheet.get_all_values()
    if not values_l:
        leaves_sheet.append_row(["Потребител", "Начало на отпуска", "Край на отпуска", "Общо дни", "Причина"])

def get_display_names(interaction: discord.Interaction) -> tuple[str, str, str]:
    """
    Връща:
      base_name -> предпочитано основно име (global_name или username)
      nickname  -> никнейм в сървъра (или "")
      display   -> "base_name (nickname)" или само base_name, ако няма ник.
    """
    user = interaction.user
    base_name = getattr(user, "global_name", None) or user.name
    nickname = ""
    if interaction.guild:
        member = interaction.guild.get_member(user.id)
        if member and member.nick:
            nickname = member.nick

    display = f"{base_name} ({nickname})" if nickname else base_name
    return base_name, nickname, display

def now_str():
    return datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")

# ------------------ Инициализация на таблиците ------------------

try:
    shifts_sheet = client.open(SHEET_NAME).worksheet("Shifts")
    leaves_sheet = client.open(SHEET_NAME).worksheet("Leaves")
    ensure_structure(shifts_sheet, leaves_sheet)
    print("✅ Google Sheets свързан успешно!")
except Exception as e:
    print(f"❌ Грешка при връзката с Google Sheets: {e}")
    exit(1)

# ------------------ Команди ------------------

@bot.tree.command(name="startshift", description="Започва смяната и записва времето на влизане")
async def startshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[STARTSHIFT] Грешка при defer: {e}")
        return

    t0 = time.time()
    user_id = str(interaction.user.id)
    base_name, nickname, display = get_display_names(interaction)
    start_shift_time = now_str()
    print(f"[STARTSHIFT] {display} ({user_id}) @ {start_shift_time}")

    try:
        records = shifts_sheet.get_all_records()  # изисква коректни заглавия
        # 1) Проверяваме за активна смяна по UserID
        active = None
        for i, row in enumerate(records, start=2):
            uid = str(row.get("UserID", "")).strip()
            end_val = row.get("Край")
            if uid == user_id and (end_val == "" or end_val is None):
                active = i
                break

        if active:
            await interaction.followup.send("❌ Вече имаш активна смяна!", ephemeral=False)
            print(f"[STARTSHIFT] Активна смяна (ред {active}) за {user_id}. {time.time()-t0:.2f}s")
            return

        # 2) Няма активна -> добавяме ред
        # Формат: ["Потребител", "Начало", "Край", "Изработено време", "UserID"]
        shifts_sheet.append_row([display, start_shift_time, "", "", user_id])
        await interaction.followup.send(f"✅ {display} започна смяната в {start_shift_time}", ephemeral=False)
        print(f"[STARTSHIFT] Записано. {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[STARTSHIFT] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при започване на смяната!", ephemeral=False)

@bot.tree.command(name="endshift", description="Приключва смяната и записва времето на излизане")
async def endshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[ENDSHIFT] Грешка при defer: {e}")
        return

    t0 = time.time()
    user_id = str(interaction.user.id)
    base_name, nickname, display = get_display_names(interaction)
    end_time = now_str()
    print(f"[ENDSHIFT] {display} ({user_id}) @ {end_time}")

    try:
        records = shifts_sheet.get_all_records()
        target_row_index = None
        start_time_str = None

        for i, row in enumerate(records, start=2):
            uid = str(row.get("UserID", "")).strip()
            end_val = row.get("Край")
            if uid == user_id and (end_val == "" or end_val is None):
                target_row_index = i
                start_time_str = row.get("Начало")
                break

        if not target_row_index:
            await interaction.followup.send("❌ Няма започната смяна за приключване!", ephemeral=False)
            print(f"[ENDSHIFT] Няма активна смяна за {user_id}. {time.time()-t0:.2f}s")
            return

        # Изчисляване на изработено време
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        worked_time = end_dt - start_dt
        hours, rem = divmod(worked_time.total_seconds(), 3600)
        minutes = int(rem // 60)
        worked_time_str = f"{int(hours)}ч {minutes}мин"

        # Обновяваме: C = Край, D = Изработено време, A = Потребител (с новия ник), E = UserID (за всеки случай)
        updates = [
            {"range": f"A{target_row_index}", "values": [[display]]},
            {"range": f"C{target_row_index}", "values": [[end_time]]},
            {"range": f"D{target_row_index}", "values": [[worked_time_str]]},
            {"range": f"E{target_row_index}", "values": [[user_id]]},
        ]
        body = {"valueInputOption": "RAW", "data": [{"range": u["range"], "values": u["values"]} for u in updates]}
        shifts_sheet.spreadsheet.values_batch_update(body)

        await interaction.followup.send(
            f"✅ {display} приключи смяната в {end_time} (⏳ {worked_time_str})\n\n"
            "💼 **Благодарим ви за днешната ви служба!**\n"
            "Ако имате проблем или неразбирателство, моля свържете се с ръководството на LSPD.",
            ephemeral=False
        )
        print(f"[ENDSHIFT] Готово (ред {target_row_index}). {time.time()-t0:.2f}s")

    except Exception as e:
        print(f"[ENDSHIFT] Необработена грешка: {e}")
        try:
            await interaction.followup.send("❌ Възникна грешка при приключване на смяната!", ephemeral=False)
        except Exception as followup_error:
            print(f"[ENDSHIFT] Грешка при followup: {followup_error}")

@bot.tree.command(name="leave", description="Заявка за отпуск (ДД.ММ.ГГГГ)")
async def leave(interaction: discord.Interaction, start_date: str, end_date: str, reason: str):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[LEAVE] Грешка при defer: {e}")
        return

    t0 = time.time()
    _, _, display = get_display_names(interaction)
    print(f"[LEAVE] {display}")

    try:
        start_dt = datetime.strptime(start_date, "%d.%m.%Y")
        end_dt = datetime.strptime(end_date, "%d.%m.%Y")
        total_days = (end_dt - start_dt).days + 1

        if total_days < 1:
            await interaction.followup.send("❌ Грешка: Крайната дата трябва да е след началната!", ephemeral=False)
            return

        current_date = datetime.now(sofia_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        min_allowed_date = current_date - timedelta(days=1)

        if start_dt < min_allowed_date:
            await interaction.followup.send(
                f"❌ Не можеш да заявиш отпуск, започващ преди {min_allowed_date.strftime('%d.%m.%Y')}! "
                "Максимум 1 ден назад е позволен.",
                ephemeral=False
            )
            return

        if not reason or reason.strip() == "":
            await interaction.followup.send("❌ Моля, предостави причина за отпуска!", ephemeral=False)
            return

        leaves_sheet.append_row([
            display,
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
            total_days,
            reason
        ])

        await interaction.followup.send(
            f"✅ {display} заяви отпуск от {start_date} до {end_date} ({total_days} дни)\n"
            f"📝 **Причина:** {reason}",
            ephemeral=False
        )
        print(f"[LEAVE] Записано. {time.time()-t0:.2f}s")

    except ValueError as ve:
        print(f"[LEAVE] ValueError: {ve}")
        await interaction.followup.send(
            "❌ Грешен формат на датите! Използвай **ДД.ММ.ГГГГ** (пример: 13.03.2025)",
            ephemeral=False
        )
    except Exception as e:
        print(f"[LEAVE] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при заявяване на отпуск!", ephemeral=False)

@bot.tree.command(name="report", description="Генерира отчет за работното време")
async def report(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[REPORT] Грешка при defer: {e}")
        return

    t0 = time.time()
    user_id = str(interaction.user.id)
    _, _, display = get_display_names(interaction)
    print(f"[REPORT] {display} ({user_id})")

    try:
        records = shifts_sheet.get_all_records()
        # Филтър по UserID за коректност
        user_records = [row for row in records if str(row.get("UserID", "")).strip() == user_id]
        if not user_records:
            await interaction.followup.send("❌ Няма записано работно време!", ephemeral=False)
            return

        report_text = f"📋 **Отчет за {display}:**\n"
        for row in user_records:
            start = row.get("Начало", "❓")
            end = row.get("Край", "❓")
            worked_time = row.get("Изработено време", "Неизвестно")
            report_text += f"📅 {start} ➝ {end} ⏳ {worked_time}\n"

        await interaction.followup.send(report_text, ephemeral=False)
        print(f"[REPORT] Готово. {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[REPORT] Грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при генериране на отчета!", ephemeral=False)

@bot.tree.command(name="documents", description="Показва важни документи за полицията")
async def documents(interaction: discord.Interaction):
    doc_links = (
        "**📜 Важни документи на LSPD:**\n\n"
        "📖 **Наказателен кодекс (Penal Code):**\n"
        "🔗 https://docs.google.com/spreadsheets/d/1vyCQWnxKUPKknOsIpiXqU_-qC8vpLaHdDQIQu22hz2s/edit?gid=0#gid=0\n\n"
        "📕 **LSPD Handbook (Ръководство):**\n"
        "🔗 https://docs.google.com/document/d/1eEsR6jwpk0Y38Yw7vr22BlB1w9HiI3qtib-uy_YkWck/edit?tab=t.aho3f2r2d6uw\n"
    )
    await interaction.response.send_message(doc_links, ephemeral=False)

# 🔥 Стартиране на бота
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Ботът е онлайн! Логнат като {bot.user}")
        print(f"✅ Синхронизирани команди: {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"❌ Грешка при синхронизация: {e}")

bot.run(TOKEN)
