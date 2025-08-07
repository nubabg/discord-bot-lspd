import os
import discord
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import time
import pytz
import json

sofia_tz = pytz.timezone("Europe/Sofia")

# ✅ Токен от Railway Variables
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌ Грешка: DISCORD_TOKEN не е намерен!")
    exit(1)

# Intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# Google Sheets
SHEET_NAME = "LSPD BOT"
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

credentials_content = os.getenv("CREDENTIALS_JSON")
if not credentials_content:
    print("❌ Грешка: CREDENTIALS_JSON не е зададен!")
    exit(1)

creds_dict = json.loads(credentials_content)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

def ensure_shifts_headers(sh):
    """
    A: Потребител (username – стабилен)
    B: Начало
    C: Край
    D: Изработено време
    E: Псевдоним (nickname – визуално)
    """
    values = sh.get_all_values()
    wanted = ["Потребител", "Начало", "Край", "Изработено време", "Псевдоним"]
    if not values:
        sh.append_row(wanted)
        return
    headers = values[0]
    # Подравняваме заглавките до A:E
    if headers[:len(wanted)] != wanted:
        sh.update("A1:E1", [wanted])
    # ако няма колона Е, създаваме празни стойности
    if len(headers) < 5 and len(values) > 1:
        sh.update(f"E2:E{len(values)}", [[""] for _ in range(len(values)-1)])

def get_identity(interaction: discord.Interaction):
    """
    Връща: username, nickname, display
    - username: стабилен ключ (interaction.user.name)
    - nickname: текущ ник в сървъра (или "")
    - display: "username (nickname)" или само username
    """
    username = interaction.user.name
    nickname = ""
    if interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        if member and member.nick:
            nickname = member.nick
    display = f"{username} ({nickname})" if nickname else username
    return username, nickname, display

try:
    shifts_sheet = client.open(SHEET_NAME).worksheet("Shifts")
    leaves_sheet = client.open(SHEET_NAME).worksheet("Leaves")
    ensure_shifts_headers(shifts_sheet)
    if not leaves_sheet.get_all_values():
        leaves_sheet.append_row(["Потребител", "Начало на отпуска", "Край на отпуска", "Общо дни", "Причина"])
    print("✅ Google Sheets свързан успешно!")
except Exception as e:
    print(f"❌ Грешка при връзката с Google Sheets: {e}")
    exit(1)

# -------------------- Команди --------------------

@bot.tree.command(name="startshift", description="Започва смяната и записва времето на влизане")
async def startshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[STARTSHIFT] defer error: {e}")
        return

    t0 = time.time()
    username, nickname, display = get_identity(interaction)
    start_shift_time = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[STARTSHIFT] user={username} nick='{nickname}' at {start_shift_time}")

    try:
        records = shifts_sheet.get_all_records()
        # проверка за активна смяна по username
        for row in records:
            if row.get("Потребител") == username and (row.get("Край") in ("", None)):
                await interaction.followup.send("❌ Вече имаш активна смяна!", ephemeral=False)
                return

        # A=username, B=start, C="", D="", E=nickname
        shifts_sheet.append_row([username, start_shift_time, "", "", nickname])
        await interaction.followup.send(f"✅ {display} започна смяната в {start_shift_time}", ephemeral=False)
        print(f"[STARTSHIFT] OK in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[STARTSHIFT] Unexpected: {e}")
        await interaction.followup.send("❌ Възникна грешка при започване на смяната!", ephemeral=False)

@bot.tree.command(name="endshift", description="Приключва смяната и записва времето на излизане")
async def endshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[ENDSHIFT] defer error: {e}")
        return

    t0 = time.time()
    username, nickname, display = get_identity(interaction)
    end_time = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ENDSHIFT] user={username} nick='{nickname}' at {end_time}")

    try:
        records = shifts_sheet.get_all_records()
        target_i = None
        start_time_str = None

        for i, row in enumerate(records, start=2):  # ред 1 = заглавки
            if row.get("Потребител") == username and (row.get("Край") in ("", None)):
                target_i = i
                start_time_str = row.get("Начало")
                break

        if not target_i:
            await interaction.followup.send("❌ Няма започната смяна за приключване!", ephemeral=False)
            print(f"[ENDSHIFT] no active shift for {username}")
            return

        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        worked = end_dt - start_dt
        h, rem = divmod(worked.total_seconds(), 3600)
        m = int(rem // 60)
        worked_str = f"{int(h)}ч {m}мин"

        # Обновяваме край и изработено време
        shifts_sheet.update(f"C{target_i}", [[end_time]])
        shifts_sheet.update(f"D{target_i}", [[worked_str]])

        # Ако никът е сменен – обнови Е
        current_nick = records[target_i-2].get("Псевдоним") or ""
        if current_nick != nickname:
            shifts_sheet.update(f"E{target_i}", [[nickname]])

        await interaction.followup.send(
            f"✅ {display} приключи смяната в {end_time} (⏳ {worked_str})\n\n"
            "💼 **Благодарим ви за днешната ви служба!**\n"
            "Ако имате проблем или неразбирателство, моля свържете се с ръководството на LSPD.",
            ephemeral=False
        )
        print(f"[ENDSHIFT] OK in {time.time()-t0:.2f}s")

    except Exception as e:
        print(f"[ENDSHIFT] Unexpected: {e}")
        try:
            await interaction.followup.send("❌ Възникна грешка при приключване на смяната!", ephemeral=False)
        except Exception as fe:
            print(f"[ENDSHIFT] followup error: {fe}")

@bot.tree.command(name="leave", description="Заявка за отпуск за определен период с причина")
async def leave(interaction: discord.Interaction, start_date: str, end_date: str, reason: str):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[LEAVE] defer error: {e}")
        return

    t0 = time.time()
    username, nickname, display = get_identity(interaction)
    print(f"[LEAVE] {display}")

    try:
        start_dt = datetime.strptime(start_date, "%d.%m.%Y")
        end_dt = datetime.strptime(end_date, "%d.%m.%Y")
        total_days = (end_dt - start_dt).days + 1

        if total_days < 1:
            await interaction.followup.send("❌ Крайната дата трябва да е след началната!", ephemeral=False)
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
            display,  # показваме username (nickname) за яснота
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
        print(f"[LEAVE] OK in {time.time()-t0:.2f}s")

    except ValueError:
        await interaction.followup.send(
            "❌ Грешен формат на датите! Използвай **ДД.ММ.ГГГГ** (пример: 13.03.2025)",
            ephemeral=False
        )
    except Exception as e:
        print(f"[LEAVE] Unexpected: {e}")
        await interaction.followup.send("❌ Възникна грешка при заявяване на отпуск!", ephemeral=False)

@bot.tree.command(name="report", description="Генерира отчет за работното време")
async def report(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[REPORT] defer error: {e}")
        return

    t0 = time.time()
    username, nickname, display = get_identity(interaction)
    print(f"[REPORT] {display}")

    try:
        records = shifts_sheet.get_all_records()
        user_records = [row for row in records if row.get("Потребител") == username]
        if not user_records:
            await interaction.followup.send("❌ Няма записано работно време!", ephemeral=False)
            return

        report_text = f"📋 **Отчет за {display}:**\n"
        for row in user_records:
            start = row.get("Начало", "❓")
            end = row.get("Край", "❓")
            worked_time = row.get("Изработено време", "Неизвестно")
            shown = f"{row.get('Потребител')} ({row.get('Псевдоним')})" if row.get("Псевдоним") else row.get("Потребител")
            report_text += f"👤 {shown} | 📅 {start} ➝ {end} ⏳ {worked_time}\n"

        await interaction.followup.send(report_text, ephemeral=False)
        print(f"[REPORT] OK in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[REPORT] Unexpected: {e}")
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

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Ботът е онлайн! Логнат като {bot.user}")
        print(f"✅ Синхронизирани команди: {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"❌ Грешка при синхронизация: {e}")

bot.run(TOKEN)
