import discord
import os
import aiohttp
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
from datetime import datetime, timezone

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
    
proxy_url = os.environ.get("PROXY_URL")
MONGO_URI = os.environ.get("MONGO_URI")
cluster = MongoClient(MONGO_URI)
db = cluster["GiveawayBot"]
giveaways_col = db["active_giveaways"]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

async def log_event(text: str):
    LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID"))
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        now = datetime.now()
        timestamp = now.strftime("%d/%m/%Y] %H:%M:%S") + f".{now.strftime('%f')[:3]}"
        try:
            await channel.send(f"`[{timestamp} || {text}`")
        except discord.HTTPException:
            pass

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, proxy=proxy_url)
        self.first_run = True
        self.session = None
    
    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        self.add_view(GiveawayEndedView(self))
        self.add_view(GiveawayView(None))
        self.check_giveaways.start()
        await self.tree.sync()
        print(f"Logged in as {self.user}")

    async def on_ready(self):
        if not self.first_run:
            return
        log_channel_id = int(os.environ.get("LOG_CHANNEL_ID"))
        channel = self.get_channel(log_channel_id)
        if channel:
            embed = discord.Embed(title="System Online", description="Bot has started.", color=0x2ecc71)
            embed.add_field(name="Version", value="v1.0.5-optimized", inline=True)
            embed.add_field(name="Latency", value=f"{round(self.latency * 1000)}ms", inline=True)
            await channel.send(embed=embed)
        self.first_run = False

    @tasks.loop(seconds=45)
    async def check_giveaways(self):
        now = datetime.now(timezone.utc)
        ended = list(giveaways_col.find({"end_time": {"$lte": now}, "ended": {"$ne": True}}))
        
        for g in ended:
            giveaways_col.update_one({"_id": g["_id"]}, {"$set": {"ended": True}})
            channel = self.get_channel(g["channel_id"])
            if not channel:
                continue

            try:
                msg = await channel.fetch_message(g["message_id"])
                await msg.delete()
            except:
                pass
            
            if len(g["entrants"]) > 0:
                try:
                    api_val = secrets.randbelow(100)
                    try:
                        async with self.session.get("https://www.random.org/integers/?num=1&min=1&max=100&col=1&base=10&format=plain&rnd=new", timeout=3) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                api_val = int(text.strip())
                    except:
                        pass

                    seed = f"{api_val}{time.time_ns()}".encode()
                    hex_hash = hashlib.sha256(seed).hexdigest()
                    short_hash = hex_hash[:12].upper()
                    winner_idx = int(hex_hash, 16) % len(g["entrants"])
                    winner_id = g["entrants"][winner_idx]

                    giveaways_col.update_one({"_id": g["_id"]}, {"$set": {"final_hash": short_hash}})
                    
                    is_final_status = g.get("is_final", False)
                    end_title = "FINAL GIVEAWAY ENDED 🎊" if is_final_status else "GIVEAWAY ENDED 🎊"
                    end_color = 0x00008B if is_final_status else 0x3498db
                    end_image = "https://i.imgur.com/8tFAxzY.png" if is_final_status else "https://i.imgur.com/BRNcUVE.png"
                    
                    embed = discord.Embed(
                        title=end_title,
                        description=f"**Winner**: <@{winner_id}>\n**Giveaway Won**: **{g['title']}**\n//////////////////////////////////////////////////",
                        color=end_color
                    )
                    embed.set_image(url=end_image)
                    embed.set_footer(text=f"Giveaway ID: {g['_id']}")
                    
                    await channel.send(embed=embed, view=GiveawayEndedView(self, g["entrants"], g["title"], g["_id"], hash_val=short_hash))
                    await channel.send(f"<@{winner_id}>")
                    await log_event(f"Giveaway {g['_id']} finished. Winner: {winner_id}")
                except Exception as e:
                    await log_event(f"Error processing end: {e}")
            else:
                await channel.send(f"Giveaway for **{g['title']}** ended with no entries.")
            await asyncio.sleep(2)

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
        entry_lines = [f"• <@{uid}>" + (f" (x{doc['entrants'].count(uid)})" if doc['entrants'].count(uid) > 1 else "") for uid in unique_entrants]
        description = "\n".join(entry_lines)
        if len(description) > 2000: description = f"Total entries: {len(doc['entrants'])}"
        await interaction.response.send_message(embed=discord.Embed(title="Final Entrants", description=description, color=0x3498db), ephemeral=True)
    
    @discord.ui.button(label="Debug", style=discord.ButtonStyle.gray, custom_id="debug_btn")
    async def debug(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            gid = interaction.message.embeds[0].footer.text.split("Giveaway ID: ")[1].strip()
            doc = giveaways_col.find_one({"_id": gid})
            hex_hash = doc.get('final_hash', '0')
            entrants_count = len(doc.get('entrants', []))
            winner_idx = int(hex_hash, 16) % entrants_count if entrants_count > 0 else 0
            embed = discord.Embed(title="Debug Menu", color=0x2f3136)
            embed.add_field(name="CORE", value=f"ID: {gid}", inline=False)
            embed.add_field(name="HASH", value=f"SHA-256: {hex_hash}", inline=False)
            embed.add_field(name="STATS", value=f"Entrants: {entrants_count}\nIndex: {winner_idx}", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            await interaction.response.send_message("Debug failed.", ephemeral=True)
        
    @discord.ui.button(label="Reroll", style=discord.ButtonStyle.red, custom_id="reroll_btn", emoji="🎲")
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("❌ No permission!", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        doc = giveaways_col.find_one({"_id": self.giveaway_id})
        if not doc or not doc.get("entrants"):
            return await interaction.followup.send("Cannot reroll.")
        
        api_val = secrets.randbelow(100)
        seed = f"{api_val}{time.time_ns()}".encode()
        hex_hash = hashlib.sha256(seed).hexdigest()
        winner_id = doc["entrants"][int(hex_hash, 16) % len(doc["entrants"])]
        
        try:
            await interaction.message.delete()
        except: pass

        win_embed = discord.Embed(title="REROLLED RESULTS 🔄", description=f"**New Winner 🎉**: <@{winner_id}>\n**Rerolled By**: {interaction.user.mention}", color=0xe74c3c)
        win_embed.set_image(url="https://i.imgur.com/iM8ByUz.png")
        win_embed.set_footer(text=f"Hash: {hex_hash[:12].upper()} | Giveaway ID: {self.giveaway_id}")
        await interaction.channel.send(embed=win_embed, view=GiveawayEndedView(self.bot, doc["entrants"], doc["title"], self.giveaway_id, hash_val=hex_hash[:12].upper()))
        await interaction.channel.send(f"<@{winner_id}>")

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
            
    @discord.ui.button(label="View Entrants", style=discord.ButtonStyle.gray, custom_id="view_btn")
    async def view_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = self.giveaway_id or interaction.message.embeds[0].footer.text.split("Giveaway ID: ")[1].strip()
        doc = giveaways_col.find_one({"_id": gid})
        if not doc or not doc["entrants"]:
            return await interaction.response.send_message("No entries yet.", ephemeral=True)
        unique_entrants = list(set(doc["entrants"]))
        entry_lines = [f"• <@{uid}>" + (f" (x{doc['entrants'].count(uid)})" if doc['entrants'].count(uid) > 1 else "") for uid in unique_entrants]
        await interaction.response.send_message(embed=discord.Embed(title="Current Entrants", description="\n".join(entry_lines)[:2000], color=0x3498db), ephemeral=True)

    @discord.ui.button(label="Enter Giveaway", style=discord.ButtonStyle.green, custom_id="enter_btn", emoji="🎉")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = self.giveaway_id or interaction.message.embeds[0].footer.text.split("Giveaway ID: ")[1].strip()
        doc = giveaways_col.find_one({"_id": gid})
        if not doc or interaction.user.id in doc["entrants"]:
            return await interaction.response.send_message("Already entered!", ephemeral=True)
        
        multiplier = 1
        role_names = [role.name for role in interaction.user.roles]
        if "🏆 x3 Entries" in role_names: multiplier = 3
        elif "🏆 x2 Entries" in role_names: multiplier = 2
        
        giveaways_col.update_one({"_id": gid}, {"$push": {"entrants": {"$each": [interaction.user.id] * multiplier}}})
        await interaction.response.send_message(f"Entered with x{multiplier} luck!", ephemeral=True)

    @discord.ui.button(label="Leave Giveaway", style=discord.ButtonStyle.red, custom_id="leave_btn")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        gid = self.giveaway_id or interaction.message.embeds[0].footer.text.split("Giveaway ID: ")[1].strip()
        giveaways_col.update_one({"_id": gid}, {"$pull": {"entrants": interaction.user.id}})
        await interaction.response.send_message("Left successfully.", ephemeral=True)

@bot.tree.command(name="creategiveaway", description="Setup a giveaway.")
@app_commands.default_permissions(administrator=True)
async def creategiveaway(interaction: discord.Interaction, title: str, description: str, hours: float, is_final: str = "n"):
    giveaway_id = str(interaction.id)
    is_final_bool = is_final.lower() == "y"
    end_ts = datetime.now(timezone.utc).timestamp() + (hours * 3600)
    
    embed = discord.Embed(title=f"{'FINAL ' if is_final_bool else ''}GIVEAWAY: 🎉 {title} 🎉", description=f"{description}\n\n**Ends:** <t:{int(end_ts)}:R>", color=0xf1c40f if is_final_bool else 0x3498db)
    embed.set_image(url="https://i.imgur.com/qnLmBhj.png" if is_final_bool else "https://i.imgur.com/qm7sTPg.png")
    embed.set_footer(text=f"Giveaway ID: {giveaway_id}")

    await interaction.response.send_message(embed=embed, view=GiveawayView(giveaway_id))
    msg = await interaction.original_response()
    giveaways_col.insert_one({"_id": giveaway_id, "message_id": msg.id, "title": title, "channel_id": interaction.channel_id, "entrants": [], "end_time": datetime.fromtimestamp(end_ts, tz=timezone.utc), "is_final": is_final_bool})

@bot.tree.command(name="testfill")
@app_commands.default_permissions(administrator=True)
async def testfill(interaction: discord.Interaction, giveaway_id: str):
    giveaways_col.update_one({"_id": giveaway_id}, {"$push": {"entrants": {"$each": [111, 222, 333]}}})
    await interaction.response.send_message("Added fake users.", ephemeral=True)

@bot.tree.command(name="cancelgiveaway")
@app_commands.default_permissions(administrator=True)
async def cancelgiveaway(interaction: discord.Interaction, giveaway_id: str, action: str):
    doc = giveaways_col.find_one({"_id": giveaway_id})
    if not doc: return await interaction.response.send_message("Not found.")
    if action.lower() == "c":
        giveaways_col.delete_one({"_id": giveaway_id})
        await interaction.response.send_message("Cancelled.")
    else:
        giveaways_col.update_one({"_id": giveaway_id}, {"$set": {"end_time": datetime.now(timezone.utc)}})
        await interaction.response.send_message("Ending soon.")

@bot.tree.command(name="shutdown")
@app_commands.default_permissions(administrator=True)
async def shutdown(interaction: discord.Interaction):
    if interaction.user.id != int(os.environ.get("DEV_ID")): return
    await interaction.response.send_message("Shutting down...")
    await bot.session.close()
    await bot.close()
    sys.exit()

if __name__ == "__main__":
    keep_alive()
    bot.run(os.environ.get("TOKEN"))
