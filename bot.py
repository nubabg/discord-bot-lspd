import os
import discord
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import time
import pytz
import json

# Настройване на часовата зона за София
sofia_tz = pytz.timezone("Europe/Sofia")

# Взимане на токена от средата
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("❌ Грешка: DISCORD_TOKEN не е намерен!")
    exit(1)

# Създаване на intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="/", intents=intents)

# --- Връзка с Google Sheets ---
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

    # Проверка и създаване/корекция на хедъри
    if not shifts_sheet.get_all_values():
        shifts_sheet.append_row(["Потребител", "Начало", "Край", "Изработено време", "Discord ID"])
    else:
        headers = shifts_sheet.row_values(1)
        # Уверяваме се, че имаме 5-та колона за ID
        if len(headers) < 5 or headers[4] != "Discord ID":
            new_headers = ["Потребител", "Начало", "Край", "Изработено време", "Discord ID"]
            shifts_sheet.update("A1:E1", [new_headers])

    if not leaves_sheet.get_all_values():
        leaves_sheet.append_row(["Потребител", "Начало на отпуска", "Край на отпуска", "Общо дни", "Причина"])
except Exception as e:
    print(f"❌ Грешка при връзката с Google Sheets: {e}")
    exit(1)

# --- Помощни функции ---
def get_username_and_nick(interaction):
    """Връща ('DiscordUsername (Nickname)', user_id) за запис в Row A + стабилно ID за проверки."""
    user = interaction.user
    username = user.name  # Discord username
    if interaction.guild:
        member = interaction.guild.get_member(user.id)
        nickname = member.nick if member and member.nick else user.name
    else:
        nickname = user.name

    # Формат Row A: Username (Nickname)
    if nickname.strip().lower() == username.strip().lower():
        display = username
    else:
        display = f"{username} ({nickname})"

    return display, str(user.id)


def _find_open_shift_row_by_id(user_id: str):
    """Връща номер на ред за отворена смяна по ID (колона E), или None."""
    try:
        cells = shifts_sheet.findall(user_id, in_column=5)
        for cell in reversed(cells):  # последният е най-нов
            end_val = shifts_sheet.cell(cell.row, 3).value  # C = 'Край'
            if not end_val:
                return cell.row
    except Exception:
        pass
    return None


def _find_open_shift_row_by_display(display: str):
    """Fallback: намира отворена смяна по текста в колона A (Username (Nickname))."""
    try:
        cells = shifts_sheet.findall(display, in_column=1)
        for cell in reversed(cells):
            end_val = shifts_sheet.cell(cell.row, 3).value
            if not end_val:
                return cell.row
    except Exception:
        pass
    return None

# --- Команди на бота ---

# 📌 Команда за започване на смяна
@bot.tree.command(name="startshift", description="Започва смяната и записва времето на влизане")
async def startshift(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

    display, user_id = get_username_and_nick(interaction)
    start_shift_time = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[STARTSHIFT] Начало за {display} (ID: {user_id})")

    try:
        # 1) Проверка по ID (колона E)
        row = _find_open_shift_row_by_id(user_id)
        # 2) Fallback: ако няма по ID, но има стар запис по display (колона A)
        if row is None:
            row = _find_open_shift_row_by_display(display)

        if row is not None:
            await interaction.followup.send("❌ Вече имаш активна смяна!", ephemeral=False)
            print(f"[STARTSHIFT] Открита активна смяна (ред {row}) за {display}")
            return

        # Запис: A=display, B=start, C=end, D=worked, E=user_id
        shifts_sheet.append_row([display, start_shift_time, "", "", user_id])
        await interaction.followup.send(f"✅ {display} започна смяната в {start_shift_time}", ephemeral=False)
        print(f"[STARTSHIFT] Записан нов ред за {display}")

    except Exception as e:
        print(f"[STARTSHIFT] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при започване на смяната!", ephemeral=False)


# 📌 Команда за приключване на смяна
@bot.tree.command(name="endshift", description="Приключва смяната и записва времето на излизане")
async def endshift(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

    display, user_id = get_username_and_nick(interaction)
    end_time_str = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ENDSHIFT] Начало за {display} (ID: {user_id})")

    try:
        # 1) Търсим последната отворена смяна по ID (колона E)
        row = _find_open_shift_row_by_id(user_id)
        # 2) Fallback: ако няма ID (стари записи), търсим по колона A (display)
        if row is None:
            row = _find_open_shift_row_by_display(display)

        if row is None:
            await interaction.followup.send("❌ Няма започната смяна за приключване!", ephemeral=False)
            print(f"[ENDSHIFT] Няма активна смяна за {display}")
            return

        # Четем старт
        start_time_str = shifts_sheet.cell(row, 2).value  # B = 'Начало'
        if not start_time_str:
            start_time_str = end_time_str

        start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")
        worked = end_dt - start_dt
        hours, rem = divmod(worked.total_seconds(), 3600)
        minutes = int(rem // 60)
        worked_str = f"{int(hours)}ч {minutes}мин"

        # Обновяване на ред A..E (дописваме и ID, ако е липсвал)
        shifts_sheet.update(f"A{row}:E{row}", [[display, start_time_str, end_time_str, worked_str, user_id]])

        await interaction.followup.send(
            f"✅ {display} приключи смяната в {end_time_str} (⏳ {worked_str})",
            ephemeral=False
        )
        print(f"[ENDSHIFT] Успешно приключване за ред {row}")

    except Exception as e:
        print(f"[ENDSHIFT] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при приключване на смяната!", ephemeral=False)


# 📌 Команда за заявка за отпуск
@bot.tree.command(name="leave", description="Заявка за отпуск за определен период с причина")
async def leave(interaction: discord.Interaction, start_date: str, end_date: str, reason: str):
    await interaction.response.defer(ephemeral=False)

    display, _ = get_username_and_nick(interaction)
    print(f"[LEAVE] Начало за {display}")

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
    except ValueError:
        await interaction.followup.send(
            "❌ Грешен формат на датите! Използвай **ДД.ММ.ГГГГ** (пример: 13.03.2025)",
            ephemeral=False
        )
    except Exception as e:
        print(f"[LEAVE] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при заявяване на отпуск!", ephemeral=False)


# 📌 Команда за генериране на отчет
@bot.tree.command(name="report", description="Генерира отчет за работното време")
async def report(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

    display, user_id = get_username_and_nick(interaction)
    print(f"[REPORT] Начало за {display} (ID: {user_id})")

    try:
        all_rows = shifts_sheet.get_all_values()
        # Филтър по ID (E колона). Ако няма такива, падаме към display (A колона).
        user_rows = [r for r in all_rows[1:] if len(r) >= 5 and r[4] == user_id]
        if not user_rows:
            user_rows = [r for r in all_rows[1:] if len(r) >= 1 and r[0] == display]

        if not user_rows:
            await interaction.followup.send("❌ Няма записано работно време!", ephemeral=False)
            return

        report_text = f"📋 **Отчет за {display}:**\n"
        for r in user_rows[-15:]:
            start = r[1] if len(r) > 1 else "❓"
            end = r[2] if len(r) > 2 else "❓"
            worked_time = r[3] if len(r) > 3 else "Неизвестно"
            report_text += f"📅 {start} ➝ {end} ⏳ {worked_time}\n"

        await interaction.followup.send(report_text, ephemeral=False)
    except Exception as e:
        print(f"[REPORT] Грешка в /report: {e}")
        await interaction.followup.send("❌ Възникна грешка при генериране на отчета!", ephemeral=False)


# 📌 Команда за документи
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


# --- Стартиране на бота ---
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Ботът е онлайн! Логнат като {bot.user}")
        print(f"✅ Синхронизирани команди: {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"❌ Грешка при синхронизация: {e}")

bot.run(TOKEN)