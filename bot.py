import os
import discord
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import time
import pytz
import json

# Часова зона
sofia_tz = pytz.timezone("Europe/Sofia")

# Token от средата
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌ Грешка: DISCORD_TOKEN не е намерен!")
    exit(1)

# Intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# --- Google Sheets ---
SHEET_NAME = "LSPD BOT"
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

credentials_content = os.getenv("CREDENTIALS_JSON")
if not credentials_content:
    print("❌ Грешка: CREDENTIALS_JSON не е зададен!")
    exit(1)

try:
    creds_dict = json.loads(credentials_content)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)

    shifts_sheet = client.open(SHEET_NAME).worksheet("Shifts")
    leaves_sheet = client.open(SHEET_NAME).worksheet("Leaves")
    print("✅ Google Sheets свързан успешно!")

    # Хедъри
    if not shifts_sheet.get_all_values():
        shifts_sheet.append_row(["Потребител", "Начало", "Край", "Изработено време"])
    if not leaves_sheet.get_all_values():
        leaves_sheet.append_row(["Потребител", "Начало на отпуска", "Край на отпуска", "Общо дни", "Причина"])
except Exception as e:
    print(f"❌ Грешка при връзката с Google Sheets: {e}")
    exit(1)

# --- Помощни функции ---
def get_username_and_display(interaction):
    """
    Връща:
      username -> стабилното Discord username (без #tag)
      display  -> какво пишем в колона A: 'username (nickname)'
    """
    username = interaction.user.name
    if interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        nickname = member.nick if member and member.nick else interaction.user.name
    else:
        nickname = interaction.user.name
    display = f"{username} ({nickname})"
    return username, display

def a_cell_matches_username(a_value: str, username: str) -> bool:
    """
    Съвпадение по username, игнорираме никнейма:
    - A == username
    - или A започва с 'username ('   -> 'username (nickname)'
    """
    if not a_value:
        return False
    return a_value == username or a_value.startswith(username + " (")

# --- Команди ---

# 📌 /startshift
@bot.tree.command(name="startshift", description="Започва смяната и записва времето на влизане")
async def startshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[STARTSHIFT] Грешка при defer: {e}")
        return

    t0 = time.time()
    username, display = get_username_and_display(interaction)
    start_shift_time = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")

    try:
        rows = shifts_sheet.get_all_values()  # само лист Shifts
        # Проверка за активна смяна по username (колона A)
        for i, row in enumerate(rows[1:], start=2):
            a = row[0] if len(row) > 0 else ""
            c = row[2] if len(row) > 2 else ""
            if a_cell_matches_username(a, username) and (c is None or c == ""):
                await interaction.followup.send("❌ Вече имаш активна смяна!", ephemeral=False)
                return

        # Запис в Shifts: A,B,C,D
        shifts_sheet.append_row([display, start_shift_time, "", ""])
        await interaction.followup.send(f"✅ {display} започна смяната в {start_shift_time}", ephemeral=False)
        print(f"[STARTSHIFT] ОК за {display} ({time.time() - t0:.2f}s)")
    except Exception as e:
        print(f"[STARTSHIFT] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при започване на смяната!", ephemeral=False)

# 📌 /endshift
@bot.tree.command(name="endshift", description="Приключва смяната и записва времето на излизане")
async def endshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[ENDSHIFT] Грешка при defer: {e}")
        return

    t0 = time.time()
    username, display = get_username_and_display(interaction)
    end_time = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")

    try:
        rows = shifts_sheet.get_all_values()  # само лист Shifts
        # Търсим отдолу-нагоре последната отворена смяна за този username
        target_row = None
        for i in range(len(rows) - 1, 0, -1):
            row = rows[i]
            a = row[0] if len(row) > 0 else ""
            c = row[2] if len(row) > 2 else ""
            if a_cell_matches_username(a, username) and (c is None or c == ""):
                target_row = i + 1  # gspread е 1-базирано
                break

        if target_row is None:
            await interaction.followup.send("❌ Няма започната смяна за приключване!", ephemeral=False)
            return

        start_time_str = shifts_sheet.cell(target_row, 2).value  # B
        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        worked_time = end_dt - start_dt
        hours, rem = divmod(worked_time.total_seconds(), 3600)
        minutes = int(rem // 60)
        worked_str = f"{int(hours)}ч {minutes}мин"

        # Обновяваме C и D в Shifts
        shifts_sheet.update(f"C{target_row}", [[end_time]])
        shifts_sheet.update(f"D{target_row}", [[worked_str]])

        await interaction.followup.send(
            f"✅ {display} приключи смяната в {end_time} (⏳ {worked_str})",
            ephemeral=False
        )
        print(f"[ENDSHIFT] ОК за {display} ({time.time() - t0:.2f}s)")
    except Exception as e:
        print(f"[ENDSHIFT] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при приключване на смяната!", ephemeral=False)

# 📌 /leave
@bot.tree.command(name="leave", description="Заявка за отпуск за определен период с причина")
async def leave(interaction: discord.Interaction, start_date: str, end_date: str, reason: str):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[LEAVE] Грешка при defer: {e}")
        return

    _, display = get_username_and_display(interaction)

    try:
        start_dt = datetime.strptime(start_date, "%d.%m.%Y")
        end_dt = datetime.strptime(end_date, "%d.%m.%Y")
        total_days = (end_dt - start_dt).days + 1
        if total_days < 1:
            await interaction.followup.send("❌ Крайната дата трябва да е след началната!", ephemeral=False)
            return

        today0 = datetime.now(sofia_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        if start_dt < (today0 - timedelta(days=1)):
            await interaction.followup.send(
                "❌ Не можеш да заявиш отпуск, започващ повече от 1 ден назад.",
                ephemeral=False
            )
            return

        if not reason.strip():
            await interaction.followup.send("❌ Моля, добави причина за отпуска.", ephemeral=False)
            return

        # Запис в лист Leaves
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
    except ValueError:
        await interaction.followup.send(
            "❌ Невалиден формат на датите. Използвай ДД.ММ.ГГГГ (пример: 13.03.2025).",
            ephemeral=False
        )
    except Exception as e:
        print(f"[LEAVE] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при заявяване на отпуск!", ephemeral=False)

# 📌 /report
@bot.tree.command(name="report", description="Генерира отчет за работното време")
async def report(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[REPORT] Грешка при defer: {e}")
        return

    username, display = get_username_and_display(interaction)

    try:
        rows = shifts_sheet.get_all_values()  # само Shifts
        user_rows = [r for r in rows[1:] if a_cell_matches_username(r[0] if r else "", username)]
        if not user_rows:
            await interaction.followup.send("❌ Няма записано работно време!", ephemeral=False)
            return

        report_text = f"📋 **Отчет за {display}:**\n"
        for r in user_rows[-15:]:
            start = r[1] if len(r) > 1 else "❓"
            end = r[2] if len(r) > 2 else "❓"
            worked = r[3] if len(r) > 3 else "Неизвестно"
            report_text += f"📅 {start} ➝ {end} ⏳ {worked}\n"

        await interaction.followup.send(report_text, ephemeral=False)
    except Exception as e:
        print(f"[REPORT] Грешка в /report: {e}")
        await interaction.followup.send("❌ Възникна грешка при генериране на отчета!", ephemeral=False)

# 📌 /documents
@bot.tree.command(name="documents", description="Показва важни документи за полицията")
async def documents(interaction: discord.Interaction):
    doc_links = (
        "**📜 Важни документи на LSPD:**\n\n"
        "📖 **Наказателен кодекс (Penal Code):**\n"
        "🔗 [Линк към документа](https://docs.google.com/spreadsheets/d/1vyCQWnxKUPKknOsIpiXqU_-qC8vpLaHdDQIQu22hz2s/edit?gid=0#gid=0)\n\n"
        "📕 **LSPD Handbook (Ръководство):**\n"
        "🔗 [Линк към документа](https://docs.google.com/document/d/1eEsR6jwpk0Y38Yw7vr22BlB1w9HiI3qtib-uy_YkWck/edit?tab=t.aho3f2r2d6uw)\n"
    )
    await interaction.response.send_message(doc_links, ephemeral=False)

# --- Стартиране ---
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Ботът е онлайн! Логнат като {bot.user}")
        print(f"✅ Синхронизирани команди: {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"❌ Грешка при синхронизация: {e}")

bot.run(TOKEN)
