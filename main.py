import discord
import os
import requests
import asyncio
import time
import hashlib
import secrets
import sys
from discord.ext import commands, tasks
from discord import app_commands
from pymongo import MongoClient
from flask import Flask
from threading import Thread
from datetime import datetime, timezone, timedelta

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"
    
def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
    
def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

MONGO_URI = os.environ.get("MONGO_URI")
cluster = MongoClient(MONGO_URI)
db = cluster["GiveawayBot"]
giveaways_col = db["active_giveaways"]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

#################################

async def log_event(text: str):
    LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID"))
    channel = bot.get_channel(LOG_CHANNEL_ID)

    if channel:
        now = datetime.now()
        timestamp = now.strftime("%d/%m/%Y] %H:%M:%S") + f".{now.strftime('%f')[:3]}"

        timestamped_text = f"`[{timestamp} || {text}`"
        await channel.send(timestamped_text)
    else:
        print(f"Logged failed: Channel not found. Message: {text}")

################################

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.first_run = True
    
    async def setup_hook(self):
        self.add_view(GiveawayEndedView(self))
        self.add_view(GiveawayView(None))
        db["active_giveaways"].create_index("end_time", expireAfterSeconds=2592000)
        self.check_giveaways.start()
        await self.tree.sync()
        print(f"Logged in as {self.user}")

    async def on_ready(self):

        if not self.first_run:
            return
        
        log_channel_id = int(os.environ.get("LOG_CHANNEL_ID"))
        channel = self.get_channel(log_channel_id)
        if channel:
            embed = discord.Embed(
                title="System Online",
                description="Bot has started/restarted.",
                color=0x2ecc71
            )
            embed.add_field(name="Version", value="v1.0.4-stable", inline=True)
            embed.add_field(name="Latency", value=f"{round(self.latency * 1000)}ms", inline=True)
            embed.set_footer(text=f"Logged in as {self.user}")
            await channel.send(embed=embed)
            await log_event("Bot has successfully started/restarted.")

        self.first_run = False

    #################################
    @tasks.loop(seconds=30)
    async def check_giveaways(self):
        now = datetime.now(timezone.utc)
        ended = giveaways_col.find({
            "end_time": {"$lte": now},
            "ended": {"$ne": True}
        })

        for g in ended:
            giveaways_col.update_one({"_id": g["_id"]}, {"$set": {"ended": True}})
            channel = self.get_channel(g["channel_id"])
            if channel:
                await log_event(f"Giveaway ended for [{g['title']}] with ID [{g['_id']}]. Attempting to delete initial giveaway interface...")
                try:
                    old_msg = await channel.fetch_message(g["message_id"])
                    await old_msg.delete()
                    await log_event(f"Deletion successful for message with ID [{g['message_id']}].")
                except:
                    await log_event("Message may have been deleted. Passing...")
                    pass
                
                await asyncio.sleep(1)

                if len(g["entrants"]) > 0:
                    try:
                        await log_event("Attempting to reach Random.org API...")
                        try:
                            res = requests.get("https://www.random.org/integers/?num=1&min=1&max=100&col=1&base=10&format=plain&rnd=new", timeout=5)
                            api_val = int(res.text.strip())
                            await log_event("Successfully reached API. Result valid.")
                        except Exception as e:
                            api_val = secrets.randbelow(100)
                            await log_event(f"Error with API: {e}. Using standard randomizer as subtitute...")

                        await log_event("Attempting randomization...")
                        seed = f"{api_val}{time.time_ns()}".encode()
                        hex_hash = hashlib.sha256(seed).hexdigest()
                        short_hash = hex_hash[:12].upper()
                        
                        winner_idx = int(hex_hash, 16) % len(g["entrants"])
                        winner_id = g["entrants"][winner_idx]
                        await log_event("Randomization successful...")

                        giveaways_col.update_one({"_id": g["_id"]}, {"$set": {"final_hash": short_hash}})
                        await log_event("Database updated.")

                        await log_event("Creating embed...")
                        embed = discord.Embed(
                            title="GIVEAWAY ENDED 🎊",
                            description=f"**Winner**: <@{winner_id}>\n**Giveaway Won**: **{g['title']}**\n//////////////////////////////////////////////////",
                            color=0x3498db
                        )
                        embed.set_image(url="https://i.imgur.com/BRNcUVE.png")
                        embed.set_footer(text=f"Giveaway ID: {g['_id']}")
                        
                        await channel.send(embed=embed, view=GiveawayEndedView(self, g["entrants"], g["title"], g["_id"], hash_val=short_hash))
                        await asyncio.sleep(1)
                        await channel.send(f"<@{winner_id}>")

                        await log_event("Message sent successfully.")
                    except Exception as e:
                        print(f"API Error: {e}")
                        await log_event(f"API Error: {e}")
                else:
                    await channel.send(f"Giveaway for **{g['title']}** ended with no entries.")
                    await log_event(f"Giveaway with title [{g['title']}] and ID [{g['_id']}] ended without entrants.")
            
            await asyncio.sleep(1)

bot = MyBot()

class GiveawayEndedView(discord.ui.View):
    def __init__(self, bot, entrants=None, title=None, giveaway_id=None, hash_val=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.entrants = entrants or []
        self.title = title or "Giveaway"
        self.giveaway_id = giveaway_id
        self.hash_val = hash_val

    @discord.ui.button(label="View Entrants", style=discord.ButtonStyle.gray, custom_id="view_ended_btn")
    async def view_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        doc = giveaways_col.find_one({"_id": self.giveaway_id})
        
        if not doc or not doc.get("entrants"):
            return await interaction.response.send_message("No entries found.", ephemeral=True)

        unique_entrants = list(set(doc["entrants"]))
        entry_lines = []
        for uid in unique_entrants:
            count = doc["entrants"].count(uid)
            multiplier_text = f" (x{count})" if count > 1 else ""
            entry_lines.append(f"• <@{uid}>{multiplier_text}")

        description = "\n".join(entry_lines)
        if len(description) > 2000:
            description = f"List too long to display. Total entries: {len(doc['entrants'])}"

        embed = discord.Embed(title="Final Entrants List", description=description, color=0x3498db)
        embed.set_footer(text=f"Total Entrants: {len(doc['entrants'])}")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    
    
    @discord.ui.button(label="Debug", style=discord.ButtonStyle.gray, custom_id="debug_btn")
    async def debug(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            footer = interaction.message.embeds[0].footer.text
            gid = footer.split("Giveaway ID: ")[1].strip()
        except:
            return await interaction.response.send_message("ID not found in footer.", ephemeral=True)

        doc = giveaways_col.find_one({"_id": gid})
        hex_hash = doc.get('final_hash', '0')
        entrants_count = len(doc.get('entrants', []))
        winner_idx = int(hex_hash, 16) % entrants_count if entrants_count > 0 else 0
        latency = round(self.bot.latency * 1000, 2)
        db_ping = cluster.admin.command('ping')['ok']
        process_time = time.process_time()
        drift = round(time.time() % 1, 4)
        pid = os.getpid()

        embed = discord.Embed(title="Debug Menu", color=0x2f3136)
        embed.add_field(name="CORE_IDENTITY", value=f"```ID: {gid}\nCID: {interaction.channel_id}```", inline=False)
        embed.add_field(name="CRYPTO_SIG", value=f"```HASH: {hex_hash}\nALGO: SHA-256\nSALT: NS_TIMESTAMP```", inline=False)
        embed.add_field(name="LATENCY_METRICS", value=f"```GATEWAY: {latency}ms\nDB_NODE: {db_ping}\nAPI_SRC: RANDOM.ORG_L3```", inline=True)
        embed.add_field(name="ARRAY_DATA", value=f"```ENTRANTS: {entrants_count}\nWIN_IDX: {winner_idx}```", inline=True)
        embed.add_field(name="SYSTEM_RESOURCES", value=f"```PID: {pid}\nCPU_TIME: {process_time}s\nMEM_STATE: VIRT_ALLOC```", inline=True)
        embed.add_field(name="NETWORK_OSI", value=f"```EXEC_DRIFT: {drift}s\nMOD_DIVISOR: {entrants_count}\nBIT_STR: 256-bit```", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.red, custom_id="reroll_btn", emoji="🎲")
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = self.giveaway_id
        if not gid:
            try:
                footer_text = interaction.message.embeds[0].footer.text
                gid = footer_text.split("Giveaway ID: ")[1].strip()
            except:
                return await interaction.response.send_message("Could not find Giveaway ID.", ephemeral=True)
        
        g = giveaways_col.find_one({"_id": gid})
        if not g:
            return await interaction.response.send_message("Giveaway not found.", ephemeral=True)

        if not interaction.user.guild_permissions.manage_messages:
            await log_event(f"Reroll button pressed by normal user [<@{interaction.user.name}>].")
            return await interaction.response.send_message("❌ No permission!", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        await log_event(f"Reroll initiated by <@{interaction.user.name}> for giveaway [{g['title']}] with ID [{g['_id']}]")

        entrants = g.get("entrants", [])
        
        if not entrants:
            await log_event(f"No entrants found for giveaway [{g['title']}] with ID [{g['_id']}]")
            return await interaction.followup.send("No entrants found.", ephemeral=True)
        
        try:
            await log_event("Attempting to fetch Random.org API...")
            try:
                res = requests.get("https://www.random.org/integers/?num=1&min=1&max=100&col=1&base=10&format=plain&rnd=new", timeout=5)
                api_val = int(res.text.strip())
                await log_event("Random.org API fetched successfully and result has been generated.")
            except Exception as e:
                api_val = secrets.randbelow(100)
                await log_event(f"Error with API: {e}. Resorting to standard randomization.")

            await log_event("Attempting randomization...")
            seed = f"{api_val}{time.time_ns()}".encode()
            hex_hash = hashlib.sha256(seed).hexdigest()
            winner_idx = int(hex_hash, 16) % len(entrants)
            winner_id = entrants[winner_idx]
            await log_event("Randomization success.")

            try:
                await interaction.message.delete()
            except:
                pass

            await asyncio.sleep(0.5)

            async for msg in interaction.channel.history(limit=3):
                if msg.author == self.bot.user and msg.content.startswith("<@"):
                    try:
                        await msg.delete()
                        break
                    except:
                        pass

            await log_event("Creating reroll embed...")
            win_embed = discord.Embed(
                title="REROLLED RESULTS 🔄",
                description=f"**New Winner 🎉**: <@{winner_id}>\n**Rerolled By**: **{interaction.user.mention}**\n//////////////////////////////////////////////////",
                color=0xe74c3c
            )
            await log_event("Fetching hash...")
            short_hash = hex_hash[:12].upper()
            win_embed.set_image(url="https://i.imgur.com/iM8ByUz.png")
            win_embed.set_footer(text=f"Hash: {short_hash} | Giveaway ID: {gid}")
            
            await interaction.channel.send(embed=win_embed, view=GiveawayEndedView(self.bot, entrants, g["title"], gid, hash_val=short_hash))
            await asyncio.sleep(1)
            await interaction.channel.send(f"<@{winner_id}>")
            await log_event("Message sent successfully.")
            await interaction.followup.send("Reroll complete.", ephemeral=True)
        except Exception as e:
            print(f"Reroll error: {e}")
            await log_event(f"Error regarding reroll process: {e}")

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
            
    @discord.ui.button(label="View Entrants", style=discord.ButtonStyle.gray, custom_id="view_btn")
    async def view_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = self.giveaway_id
        if not gid:
            try:
                footer = interaction.message.embeds[0].footer.text
                gid = footer.split("Giveaway ID: ")[1].strip()
            except:
                return await interaction.response.send_message("Could not resolve Giveaway ID", ephemeral=True)

        doc = giveaways_col.find_one({"_id": gid})
        if not doc or not doc["entrants"]:
            return await interaction.response.send_message("No entries yet.", ephemeral=True)

        unique_entrants = list(set(doc["entrants"]))
        entry_lines = []
        for uid in unique_entrants:
            count = doc["entrants"].count(uid)
            multiplier_text = f" (x{count})" if count > 1 else ""
            entry_lines.append(f"• <@{uid}>{multiplier_text}")

        embed = discord.Embed(title="Current Entrants", description="\n".join(entry_lines), color=0x3498db)
        embed.set_footer(text=f"Total entrants: {len(doc['entrants'])}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.green, custom_id="enter_btn", emoji="🎉")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await log_event(f"User <@{interaction.user.name}> attempting to enter giveaway...")
        gid = self.giveaway_id
        if not gid:
            try:
                footer = interaction.message.embeds[0].footer.text
                gid = footer.split("Giveaway ID: ")[1].strip()
            except:
                return await interaction.response.send_message("Error: Could not resolve Giveaway ID", ephemeral=True)

        doc = giveaways_col.find_one({"_id": gid})
        if not doc:
            return await interaction.response.send_message("Data not found.", ephemeral=True)

        if interaction.user.id in doc["entrants"]:
            return await interaction.response.send_message("You are already in!", ephemeral=True)

        multiplier = 1
        luck_text = ""
        role_names = [role.name for role in interaction.user.roles]
        if "🏆 x3 Entries" in role_names:
            multiplier = 3
            luck_text = " with x3 luck"
        elif "🏆 x2 Entries" in role_names:
            multiplier = 2
            luck_text = " with x2 luck"

        entry_list = [interaction.user.id] * multiplier
        giveaways_col.update_one({"_id": gid}, {"$push": {"entrants": {"$each": entry_list}}})
        await interaction.response.send_message(f"You have entered the giveaway{luck_text}!", ephemeral=True)
        await log_event(f"User {interaction.user.name} entered giveaway {gid}.")

    @discord.ui.button(label="Leave Giveaway", style=discord.ButtonStyle.red, custom_id="leave_btn")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = self.giveaway_id
        if not gid:
            try:
                gid = interaction.message.embeds[0].footer.text.split("Giveaway ID: ")[1].strip()
            except:
                return await interaction.response.send_message("ID not found.", ephemeral=True)

        doc = giveaways_col.find_one({"_id": gid})
        if not doc:
            return await interaction.response.send_message("Giveaway not found.", ephemeral=True)

        if interaction.user.id not in doc["entrants"]:
            return await interaction.response.send_message("You haven't joined this giveaway!", ephemeral=True)

        giveaways_col.update_one(
            {"_id": gid},
            {"$pull": {"entrants": interaction.user.id}}
        )

        await interaction.response.send_message("Left the giveaway successfully!", ephemeral=True)
        await log_event(f"User {interaction.user.name} left giveaway {gid}.")

#################################

@bot.tree.command(name="creategiveaway", description="Setup a giveaway.")
@app_commands.default_permissions(administrator=True, manage_webhooks=True)
async def creategiveaway(interaction: discord.Interaction, title: str, description: str, hours: float):
    giveaway_id = str(interaction.id)

    start_time = datetime.now(timezone.utc).timestamp()
    end_timestamp = start_time + (hours * 3600)
    
    embed = discord.Embed(
        title=f"GIVEAWAY: 🎉 {title} 🎉",
        description=f"{description}\n\n**Ends:** <t:{int(end_timestamp)}:R>",
        color=0x3498db
    )
    embed.set_image(url="https://i.imgur.com/qm7sTPg.png")
    embed.set_footer(text=f"Giveaway ID: {giveaway_id}")

    await interaction.response.send_message(embed=embed, view=GiveawayView(giveaway_id))
    
    msg = await interaction.original_response()
    giveaways_col.insert_one({
        "_id": giveaway_id,
        "message_id": msg.id,
        "title": title,
        "channel_id": interaction.channel_id,
        "entrants": [],
        "end_time": datetime.fromtimestamp(end_timestamp, tz=timezone.utc)
    })
    
    await log_event(f"Giveaway successfully created with title [{title}] and ID [{giveaway_id}]")

############################################

@bot.tree.command(name="testfill", description="Fill a giveaway with 5 fake entrants.")
@app_commands.default_permissions(administrator=True, manage_webhooks=True)
async def testfill(interaction: discord.Interaction, giveaway_id: str):
    g = giveaways_col.find_one({"_id": giveaway_id})
    if not g:
        return await interaction.response.send_message("Giveaway not found.", ephemeral=True)
        
    fake_ids = [111111111111111111, 222222222222222222, 333333333333333333, 444444444444444444, 555555555555555555]
    giveaways_col.update_one({"_id": giveaway_id}, {"$push": {"entrants": {"$each": fake_ids}}})
    await interaction.response.send_message(f"Added 5 fake people to giveaway `{giveaway_id}`!", ephemeral=True)
    await log_event(f"Giveaway {giveaway_id} filled with 5 fake users.")

##########################################

@bot.tree.command(name="cancelgiveaway", description="Cancel or End the giveaway (C/E).")
@app_commands.default_permissions(administrator=True, manage_webhooks=True)
async def cancelgiveaway(interaction: discord.Interaction, giveaway_id: str, action: str):
    action = action.lower()

    if action not in ["e", "c"]:
        return await interaction.response.send_message("Invalid argument || Use E to end and C to cancel.", ephemeral=True)

    doc = giveaways_col.find_one({"_id": giveaway_id})
    if not doc:
        return await interaction.response.send_message("Giveaway not found.", ephemeral=True)
    giveaway_title = doc["title"]
    
    if action == "c":
        await interaction.response.send_message(f"**__Cancelling Giveaway__**\n\n- Title: {giveaway_title}\n- ID: {giveaway_id}")
        try:
            channel = bot.get_channel(doc["channel_id"])
            msg = await channel.fetch_message(doc["message_id"])
            await msg.delete()
        except:
            pass
        
        giveaways_col.delete_one({"_id": giveaway_id})
        await log_event(f"Giveaway {giveaway_id} cancelled and purged.")

    elif action == "e":
        giveaways_col.update_one({"_id": giveaway_id}, {"$set": {"end_time": datetime.now(timezone.utc)}})
        await interaction.response.send_message(f"**__Force Ending Giveaway__**\n\n- Title: {giveaway_title}\n- ID: {giveaway_id}")
        await log_event(f"Giveaway {giveaway_id} force-ended.")

######################################

@bot.tree.command(name="shutdown", description="Emergency Stop (DEV ONLY))")
@app_commands.default_permissions(administrator=True, manage_webhooks=True)
async def shutdown(interaction: discord.Interaction):
    if interaction.user.id != int(os.environ.get("DEV_ID")):
        return await interaction.response.send_message("You are not authorized to run this command.", ephemeral=True)

    await interaction.response.send_message("**__Bot Shutdown__**\n\n> Bot shutdown initiated by <@786598715204829244>")
    await log_event("EMERGENCY SHUTDOWN initiated.")
    
    await asyncio.sleep(1)
    await bot.close()
    sys.exit()

proxy_url = os.environ.get("PROXY_URL")

if __name__ == "__main__":
    keep_alive()
    bot.run(os.environ.get("TOKEN"), proxy=proxy_url)













