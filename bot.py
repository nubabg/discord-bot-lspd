import os
import discord
from discord.ext import commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from dotenv import load_dotenv
import time
import pytz
sofia_tz = pytz.timezone("Europe/Sofia")


# ⛔ Премахни dotenv! Railway няма нужда от него
# from dotenv import load_dotenv
# load_dotenv()

# ✅ Взимане на токена от средата (Railway Variables)
TOKEN = os.getenv("DISCORD_TOKEN")

# Проверка дали токенът е зареден
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
import json

credentials_content = os.getenv("CREDENTIALS_JSON")
if not credentials_content:
    print("❌ Грешка: CREDENTIALS_JSON не е зададен!")
    exit(1)

creds_dict = json.loads(credentials_content)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

try:
    shifts_sheet = client.open(SHEET_NAME).worksheet("Shifts")
    leaves_sheet = client.open(SHEET_NAME).worksheet("Leaves")
    print("✅ Google Sheets свързан успешно!")

    if not shifts_sheet.get_all_values():
        shifts_sheet.append_row(["Потребител", "Начало", "Край", "Изработено време"])

    if not leaves_sheet.get_all_values():
        leaves_sheet.append_row(["Потребител", "Начало на отпуска", "Край на отпуска", "Общо дни", "Причина"])
except Exception as e:
    print(f"❌ Грешка при връзката с Google Sheets: {e}")
    exit(1)



# Функция за получаване на името в сървъра
def get_nickname(interaction):
    if interaction.guild:
        member = interaction.guild.get_member(interaction.user.id)
        return member.nick if member and member.nick else interaction.user.name
    return interaction.user.name

# 📌 Команда за започване на смяна
@bot.tree.command(name="startshift", description="Започва смяната и записва времето на влизане")
async def startshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[STARTSHIFT] Грешка при defer: {e}")
        return

    start_time = time.time()
    user = get_nickname(interaction)
    start_shift_time = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[STARTSHIFT] Начало на обработка за {user} в {start_shift_time}")

    try:
        records = shifts_sheet.get_all_records()
        print(f"[STARTSHIFT] Извличане на записи за {user} завърши за {time.time() - start_time:.2f} секунди")
        for row in records:
            if row.get("Потребител") == user and row.get("Край") == "":
                await interaction.followup.send("❌ Вече имаш активна смяна!", ephemeral=False)
                print(f"[STARTSHIFT] Активна смяна открита за {user}, завърши за {time.time() - start_time:.2f} секунди")
                return

        shifts_sheet.append_row([user, start_shift_time, "", ""])
        await interaction.followup.send(f"✅ {user} започна смяната в {start_shift_time}", ephemeral=False)
        print(f"[STARTSHIFT] Успешно записване за {user}, завърши за {time.time() - start_time:.2f} секунди")
    except Exception as e:
        print(f"[STARTSHIFT] Необработена грешка: {e}")
        await interaction.followup.send("❌ Възникна грешка при започване на смяната!", ephemeral=False)

# 📌 Команда за приключване на смяна
@bot.tree.command(name="endshift", description="Приключва смяната и записва времето на излизане")
async def endshift(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[ENDSHIFT] Грешка при defer: {e}")
        return

    start_time = time.time()
    user = get_nickname(interaction)
    end_time = datetime.now(sofia_tz).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ENDSHIFT] Начало на обработка за {user} в {end_time}")

    try:
        records = shifts_sheet.get_all_records()
        print(f"[ENDSHIFT] Извличане на записи за {user} завърши за {time.time() - start_time:.2f} секунди")
        for i, row in enumerate(records, start=2):
            if row.get("Потребител") == user and (row.get("Край") == "" or row.get("Край") is None):
                start_time_str = row.get("Начало")
                start_time_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                end_time_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
                worked_time = end_time_dt - start_time_dt
                worked_hours, remainder = divmod(worked_time.total_seconds(), 3600)
                worked_minutes = remainder // 60
                worked_time_str = f"{int(worked_hours)}ч {int(worked_minutes)}мин"
                try:
                    shifts_sheet.update(range_name=f"C{i}", values=[[end_time]])
                    shifts_sheet.update(range_name=f"D{i}", values=[[worked_time_str]])
                    print(f"[ENDSHIFT] Записване на край за {user} завърши за {time.time() - start_time:.2f} секунди")
                    await interaction.followup.send(
                        f"✅ {user} приключи смяната в {end_time} (⏳ {worked_time_str})\n\n"
                        "💼 **Благодарим ви за днешната ви служба!**\n"
                        "Ако имате проблем или неразбирателство, моля свържете се с ръководството на LSPD.",
                        ephemeral=False
                    )
                    print(f"[ENDSHIFT] Успешно приключване за {user}, завърши за {time.time() - start_time:.2f} секунди")
                    return
                except gspread.exceptions.APIError as e:
                    print(f"[ENDSHIFT] Грешка при обновяване на Google Sheets: {e}")
                    await interaction.followup.send("❌ Възникна проблем с Google Sheets!", ephemeral=False)
                    return

        await interaction.followup.send("❌ Няма започната смяна за приключване!", ephemeral=False)
        print(f"[ENDSHIFT] Няма активна смяна за {user}, завърши за {time.time() - start_time:.2f} секунди")
    except Exception as e:
        print(f"[ENDSHIFT] Необработена грешка: {e}")
        try:
            await interaction.followup.send("❌ Възникна грешка при приключване на смяната!", ephemeral=False)
        except Exception as followup_error:
            print(f"[ENDSHIFT] Грешка при изпращане на followup: {followup_error}")

# 📌 Команда за заявка за отпуск
@bot.tree.command(name="leave", description="Заявка за отпуск за определен период с причина")
async def leave(interaction: discord.Interaction, start_date: str, end_date: str, reason: str):
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[LEAVE] Грешка при defer: {e}")
        return

    start_time = time.time()
    user = get_nickname(interaction)
    print(f"[LEAVE] Начало на обработка за {user}")

    try:
        start_dt = datetime.strptime(start_date, "%d.%m.%Y")
        end_dt = datetime.strptime(end_date, "%d.%m.%Y")
        total_days = (end_dt - start_dt).days + 1

        if total_days < 1:
            await interaction.followup.send("❌ Грешка: Крайната дата трябва да е след началната!", ephemeral=False)
            print(f"[LEAVE] Грешка в датите за {user}, завърши за {time.time() - start_time:.2f} секунди")
            return

        current_date = datetime.now(sofia_tz).replace(hour=0, minute=0, second=0, microsecond=0)
        min_allowed_date = current_date - timedelta(days=1)

        if start_dt < min_allowed_date:
            await interaction.followup.send(
                f"❌ Не можеш да заявиш отпуск, започващ преди {min_allowed_date.strftime('%d.%m.%Y')}! "
                "Максимум 1 ден назад е позволен.",
                ephemeral=False
            )
            print(f"[LEAVE] Грешка: дата преди позволеното за {user}, завърши за {time.time() - start_time:.2f} секунди")
            return

        if not reason or reason.strip() == "":
            await interaction.followup.send("❌ Моля, предостави причина за отпуска!", ephemeral=False)
            print(f"[LEAVE] Грешка: липсваща причина за {user}, завърши за {time.time() - start_time:.2f} секунди")
            return

        leaves_sheet.append_row([
            user,
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
            total_days,
            reason
        ])
        print(f"[LEAVE] Записване на отпуск за {user} завърши за {time.time() - start_time:.2f} секунди")

        await interaction.followup.send(
            f"✅ {user} заяви отпуск от {start_date} до {end_date} ({total_days} дни)\n"
            f"📝 **Причина:** {reason}",
            ephemeral=False
        )
        print(f"[LEAVE] Успешно записване за {user}, завърши за {time.time() - start_time:.2f} секунди")
    except ValueError as ve:
        print(f"[LEAVE] Грешка в /leave (ValueError): {ve}")
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
    try:
        await interaction.response.defer(ephemeral=False)
    except Exception as e:
        print(f"[REPORT] Грешка при defer: {e}")
        return

    start_time = time.time()
    user = get_nickname(interaction)
    print(f"[REPORT] Начало на обработка за {user}")

    try:
        records = shifts_sheet.get_all_records()
        print(f"[REPORT] Извличане на записи за {user} завърши за {time.time() - start_time:.2f} секунди")
        user_records = [row for row in records if row.get("Потребител") == user]
        if not user_records:
            await interaction.followup.send("❌ Няма записано работно време!", ephemeral=False)
            print(f"[REPORT] Няма записи за {user}, завърши за {time.time() - start_time:.2f} секунди")
            return
        report_text = f"📋 **Отчет за работното време на {user}:**\n"
        for row in user_records:
            start = row.get("Начало", "❓")
            end = row.get("Край", "❓")
            worked_time = row.get("Изработено време", "Неизвестно")
            report_text += f"📅 {start} ➝ {end} ⏳ {worked_time}\n"
        await interaction.followup.send(report_text, ephemeral=False)
        print(f"[REPORT] Успешно генериране на отчет за {user}, завърши за {time.time() - start_time:.2f} секунди")
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

# 🔥 Стартиране на бота
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Ботът е онлайн! Логнат като {bot.user}")
        print(f"✅ Синхронизирани команди: {[cmd.name for cmd in synced]}")
    except Exception as e:
        print(f"❌ Грешка при синхронизация: {e}")

bot.run(TOKEN)  # Използваме заредения TOKEN от .env