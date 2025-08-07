import os
import json
import time
from datetime import datetime, timedelta

import discord
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pytz

# ===================== CONFIG =====================
SOFIA_TZ = pytz.timezone("Europe/Sofia")

# Discord token from env (Railway variable)
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌ Грешка: DISCORD_TOKEN не е намерен в променливите на средата!")
    raise SystemExit(1)

# Google credentials from env (JSON text pasted in Railway variable)
CREDENTIALS_JSON = os.getenv("CREDENTIALS_JSON")
if not CREDENTIALS_JSON:
    print("❌ Грешка: CREDENTIALS_JSON не е намерен в променливите на средата!")
    raise SystemExit(1)

# Spreadsheet URL from env (точният линк към файла)
SHEET_URL = os.getenv("SHEET_URL")
if not SHEET_URL:
    print("❌ Грешка: SHEET_URL не е намерен в променливите на средата! (постави целия URL от Google Sheets)")
    raise SystemExit(1)

# ===================== DISCORD SETUP =====================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="/", intents=intents)

# ===================== GOOGLE SHEETS =====================
try:
    creds_dict = json.loads(CREDENTIALS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict,
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gclient = gspread.authorize(creds)

    sheet = gclient.open_by_url(SHEET_URL)
    shifts_sheet = sheet.worksheet("Shifts")
    leaves_sheet = sheet.worksheet("Leaves")

    print(f"✅ Google Sheets свързан успешно → {sheet.url}")

    # Ensure headers (and extra nickname column E)
    def ensure_shifts_headers():
        wanted = ["Потребител", "Начало", "Край", "Изработено време", "Псевдоним"]
        values = shifts_sheet.get_all_values()
        if not values:
            shifts_sheet.append_row(wanted)
            return
        headers = values[0]
        if headers[: len(wanted)] != wanted:
            shifts_sheet.update("A1:E1", [wanted])
        if len(values) > 1 and (len(headers) < 5):
            shifts_sheet.update(f"E2:E{len(values)}", [[""] for _ in range(len(values) - 1)])

    def ensure_leaves_headers():
        if not leaves_sheet.get_all_values():
            leaves_sheet.append_row(
                ["Потребител", "Начало на отпуска", "Край на отпуска", "Общо дни", "Причина"]
            )

    ensure_shifts_headers()
    ensure_leaves_headers()

except Exception as e:
    print(f"❌ Грешка при връзката с Google Sheets: {e}")
    raise SystemExit(1)

# ===================== HELPERS =====================

def now_str():
    return datetime.now(SOFIA_TZ).strftime("%Y-%m-%d %H:%M:%S")


def get_identity(interaction: discord.Interaction):
    """Връща (username, nickname, display). Username е стабилен ключ за търсене.
    Никът е чисто визуален и се пази/актуализира в колона E.
    """
    username = interaction.user.name
    nickname = ""
    if interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        if member and member.nick:
            nickname = member.nick
    display = f"{username} ({nickname})" if nickname else username
    return username, nickname, display


# ===================== COMMANDS =====================
@bot.tree.command(name="startshift", description="Започва смяна и записва време на влизане")
async def startshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[STARTSHIFT] defer error: {e}")
        return

    t0 = time.time()
    username, nickname, display = get_identity(interaction)
    started = now_str()

    try:
        records = shifts_sheet.get_all_records()
        # Проверка за активна смяна по username (колона A)
        for row in records:
            if row.get("Потребител") == username and (row.get("Край") in ("", None)):
                await interaction.followup.send("❌ Вече имаш активна смяна!", ephemeral=False)
                return

        # Сигурно вмъкване в следващия ред (избягва странности с append_row)
        values = shifts_sheet.get_all_values()
        next_row = len(values) + 1
        shifts_sheet.update(f"A{next_row}:E{next_row}", [[username, started, "", "", nickname]])

        await interaction.followup.send(f"✅ {display} започна смяната в {started}", ephemeral=False)
        print(f"[STARTSHIFT] OK in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[STARTSHIFT] Unexpected: {e}")
        await interaction.followup.send("❌ Възникна грешка при започване на смяната!", ephemeral=False)


@bot.tree.command(name="endshift", description="Приключва смяна и записва време на излизане")
async def endshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[ENDSHIFT] defer error: {e}")
        return

    t0 = time.time()
    username, nickname, display = get_identity(interaction)
    ended = now_str()

    try:
        records = shifts_sheet.get_all_records()
        target_i = None
        start_time_str = None

        for i, row in enumerate(records, start=2):  # ред 1 = заглавия
            if row.get("Потребител") == username and (row.get("Край") in ("", None)):
                target_i = i
                start_time_str = row.get("Начало")
                break

        if not target_i:
            await interaction.followup.send("❌ Няма започната смяна за приключване!", ephemeral=False)
            print(f"[ENDSHIFT] no active shift for {username}")
            return

        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(ended, "%Y-%m-%d %H:%M:%S")
        worked = end_dt - start_dt
        hours, rem = divmod(worked.total_seconds(), 3600)
        minutes = int(rem // 60)
        worked_str = f"{int(hours)}ч {minutes}мин"

        # Обнови край, изработено време и (ако е сменен) никнейм
        shifts_sheet.update(f"C{target_i}", [[ended]])
        shifts_sheet.update(f"D{target_i}", [[worked_str]])
        current_nick = records[target_i - 2].get("Псевдоним") or ""
        if current_nick != nickname:
            shifts_sheet.update(f"E{target_i}", [[nickname]])

        await interaction.followup.send(
            f"✅ {display} приключи смяната в {ended} (⏳ {worked_str})\n\n"
            "💼 **Благодарим ви за днешната ви служба!**\n"
            "Ако имате проблем или неразбирателство, моля свържете се с ръководството на LSPD.",
            ephemeral=False,
        )
        print(f"[ENDSHIFT] OK in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[ENDSHIFT] Unexpected: {e}")
        try:
            await interaction.followup.send("❌ Възникна грешка при приключване на смяната!", ephemeral=False)
        except Exception as fe:
            print(f"[ENDSHIFT] followup error: {fe}")


@bot.tree.command(name="leave", description="Заявка за отпуск за период с причина (ДД.ММ.ГГГГ)")
async def leave(interaction: discord.Interaction, start_date: str, end_date: str, reason: str):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[LEAVE] defer error: {e}")
        return

    username, nickname, display = get_identity(interaction)

    try:
        start_dt = datetime.strptime(start_date, "%d.%m.%Y")
        end_dt = datetime.strptime(end_date, "%d.%m.%Y")
        total_days = (end_dt - start_dt).days + 1
        if total_days < 1:
            await interaction.followup.send("❌ Крайната дата трябва да е след началната!", ephemeral=False)
            return

        current_date = datetime.now(SOFIA_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        min_allowed_date = current_date - timedelta(days=1)
        if start_dt < min_allowed_date:
            await interaction.followup.send(
                f"❌ Не можеш да заявиш отпуск, започващ преди {min_allowed_date.strftime('%d.%m.%Y')}! "
                "Максимум 1 ден назад е позволен.",
                ephemeral=False,
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
            reason,
        ])

        await interaction.followup.send(
            f"✅ {display} заяви отпуск от {start_date} до {end_date} ({total_days} дни)\n"
            f"📝 **Причина:** {reason}",
            ephemeral=False,
        )
    except ValueError:
        await interaction.followup.send(
            "❌ Грешен формат на датите! Използвай **ДД.ММ.ГГГГ** (пример: 13.03.2025)",
            ephemeral=False,
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

    username, nickname, display = get_identity(interaction)

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
            shown = (
                f"{row.get('Потребител')} ({row.get('Псевдоним')})" if row.get("Псевдоним") else row.get("Потребител")
            )
            report_text += f"👤 {shown} | 📅 {start} ➝ {end} ⏳ {worked_time}\n"

        await interaction.followup.send(report_text, ephemeral=False)
    except Exception as e:
        print(f"[REPORT] Unexpected: {e}")
        await interaction.followup.send("❌ Възникна грешка при генериране на отчета!", ephemeral=False)


@bot.tree.command(name="documents", description="Важни документи на LSPD")
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


if __name__ == "__main__":
    bot.run(TOKEN)
