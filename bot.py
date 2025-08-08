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

    # Проверка и създаване на хедъри, ако липсват
    if not shifts_sheet.get_all_values():
        shifts_sheet.append_row(["Потребител", "Начало", "Край", "Изработено време"])

    if not leaves_sheet.get_all_values():
        leaves_sheet.append_row(["Потребител", "Начало на отпуска", "Край на отпуска", "Общо дни", "Причина"])
except Exception as e:
    print(f"❌ Грешка при връзката с Google Sheets: {e}")
    exit(1)

# --- Помощни функции ---
def get_nickname(interaction):
    if interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        return member.nick if member and member.nick else interaction.user.name
    return interaction.user.name

# --- Команди на бота (КОРИГИРАНИ ВЕРСИИ) ---

# 📌 Команда за започване на смяна
@bot.tree.command(name="startshift", description="Започва смяната и записва времето на влизане")
async def startshift(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

    user_id = str(interaction.user.id)
    user_nickname = get_nickname(interaction)
    # Форматираме текста за първата колона
    user_identifier = f"{user_id} ({user_nickname})"
    start_shift_time = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"[STARTSHIFT] Начало на обработка за {user_identifier}")

    try:
        # Търсим по ID, за да видим дали има активна смяна
        all_cells = shifts_sheet.findall(user_id, in_column=1)
        for cell in all_cells:
            # "Край" е в колона C (индекс 3)
            end_time_val = shifts_sheet.cell(cell.row, 3).value 
            if end_time_val is None or end_time_val == "":
                await interaction.followup.send("❌ Вече имаш активна смяна!", ephemeral=False)
                print(f"[STARTSHIFT] Активна смяна открита за {user_identifier}")
                return

        # Записваме новия ред
        shifts_sheet.append_row([user_identifier, start_shift_time, "", ""])
        await interaction.followup.send(f"✅ {user_nickname} започна смяната в {start_shift_time}", ephemeral=False)
        print(f"[STARTSHIFT] Успешно записване за {user_identifier}")

    except Exception as e:
        print(f"[STARTSHIFT] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при започване на смяната!", ephemeral=False)

# 📌 Команда за приключване на смяна
@bot.tree.command(name="endshift", description="Приключва смяната и записва времето на излизане")
async def endshift(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

    user_id = str(interaction.user.id)
    user_nickname = get_nickname(interaction)
    # Форматираме новия идентификатор, за да го запишем
    user_identifier = f"{user_id} ({user_nickname})"
    end_time_str = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"[ENDSHIFT] Начало на обработка за {user_identifier}")

    try:
        # Намираме всички редове, които съдържат ID-то на потребителя
        all_user_cells = shifts_sheet.findall(user_id, in_column=1)

        # Търсим последния отворен шифт отзад-напред
        for cell in reversed(all_user_cells):
            row_values = shifts_sheet.row_values(cell.row)
            # "Край" е в трета колона (индекс 2)
            if len(row_values) < 3 or row_values[2] == "":
                start_time_str = row_values[1] # "Начало" е във втора колона
                start_time_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                end_time_dt = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M:%S")

                worked_time = end_time_dt - start_time_dt
                worked_hours, remainder = divmod(worked_time.total_seconds(), 3600)
                worked_minutes = remainder // 60
                worked_time_str = f"{int(worked_hours)}ч {int(worked_minutes)}мин"
                
                # Актуализираме потребителя, края на смяната и изработеното време
                shifts_sheet.update(f"A{cell.row}:D{cell.row}", [[user_identifier, start_time_str, end_time_str, worked_time_str]])
                
                await interaction.followup.send(
                    f"✅ {user_nickname} приключи смяната в {end_time_str} (⏳ {worked_time_str})\n\n"
                    "💼 **Благодарим ви за днешната ви служба!**",
                    ephemeral=False
                )
                print(f"[ENDSHIFT] Успешно приключване за {user_identifier}")
                return

        await interaction.followup.send("❌ Няма започната смяна за приключване!", ephemeral=False)
        print(f"[ENDSHIFT] Няма активна смяна за {user_identifier}")

    except Exception as e:
        print(f"[ENDSHIFT] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при приключване на смяната!", ephemeral=False)

# 📌 Команда за заявка за отпуск
@bot.tree.command(name="leave", description="Заявка за отпуск за определен период с причина")
async def leave(interaction: discord.Interaction, start_date: str, end_date: str, reason: str):
    await interaction.response.defer(ephemeral=False)

    user_nickname = get_nickname(interaction)
    print(f"[LEAVE] Начало на обработка за {user_nickname}")

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

        # Записваме отпуска с прякора, тъй като тук няма нужда от ID
        leaves_sheet.append_row([
            user_nickname,
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
            total_days,
            reason
        ])

        await interaction.followup.send(
            f"✅ {user_nickname} заяви отпуск от {start_date} до {end_date} ({total_days} дни)\n"
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

    user_id = str(interaction.user.id)
    user_nickname = get_nickname(interaction)
    print(f"[REPORT] Начало на обработка за {user_nickname} (ID: {user_id})")

    try:
        # Взимаме всички редове и филтрираме тези, които съдържат ID-то на потребителя
        all_records = shifts_sheet.get_all_values()
        # Пропускаме хедъра (първия ред) и търсим по ID
        user_records = [row for row in all_records[1:] if row and row[0].startswith(user_id)]
        
        if not user_records:
            await interaction.followup.send("❌ Няма записано работно време!", ephemeral=False)
            return

        report_text = f"📋 **Отчет за работното време на {user_nickname}:**\n"
        for row in user_records[-15:]: # Показваме последните 15 записа
            start = row[1] if len(row) > 1 else "❓"
            end = row[2] if len(row) > 2 else "❓"
            worked_time = row[3] if len(row) > 3 else "Неизвестно"
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