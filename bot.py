import discord
from discord import app_commands
import json
import random
import os
from fuzzywuzzy import process
from flask import Flask
from threading import Thread

# Keep alive web server
app = Flask('')

@app.route('/')
def home():
    return "I'm alive"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()

# Load JSON helpers
def load_json(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)

def save_json(file_path, data):
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)

# Load your data
cocktails = load_json('drinks.json')
users = load_json('users.json')
servers = load_json('servers.json')

# Discord setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    print(f'Bot is ready as {client.user}')
    try:
        synced = await tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Failed to sync commands: {e}')

@client.event
async def on_message(message):
    if message.author.bot:
        return

    server_id = str(message.guild.id)
    channel_id = str(message.channel.id)

    if server_id in servers and servers[server_id]["bar_channel"] == channel_id:
        user_id = str(message.author.id)
        if user_id not in users:
            users[user_id] = {"drinks": [], "messages": 0}
            first_drink = random.choice(list(cocktails.keys()))
            users[user_id]["drinks"].append(first_drink)
            await message.channel.send(f"Welcome to the bar, {message.author.mention}. Here's your first drink: {cocktails[first_drink]['name']} 🍸")
            save_json("users.json", users)
            return

        users[user_id]["messages"] += 1
        if users[user_id]["messages"] >= 5:
            users[user_id]["messages"] = 0
            if random.random() < 0.5:
                drink_name = random.choices(
                    population=list(cocktails.keys()),
                    weights=[80 if cocktails[d]['rarity'] == 'Common' else 19 if cocktails[d]['rarity'] == 'Rare' else 1 for d in cocktails],
                    k=1
                )[0]
                users[user_id]["drinks"].append(drink_name)
                await message.channel.send(f"{message.author.mention}, you earned a new drink: {cocktails[drink_name]['name']}! 🥂")
        save_json("users.json", users)

@tree.command(name="inventory", description="View your drink collection.")
async def inventory(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user_data = users.get(user_id, {"drinks": []})
    drinks = user_data["drinks"]
    total = len(cocktails)
    drink_names = [cocktails[d]["name"] for d in drinks if d in cocktails]
    message = f"You own {len(drinks)}/{total} drinks:\n" + "\n".join(drink_names) if drink_names else "You have no drinks yet."
    await interaction.response.send_message(message, ephemeral=True)

@tree.command(name="cocktail", description="Get a random cocktail recipe.")
async def cocktail(interaction: discord.Interaction):
    drink_name = random.choice(list(cocktails.keys()))
    drink = cocktails[drink_name]
    response = f"**{drink['name']}** ({drink['rarity']})\n{drink['recipe']}"
    await interaction.response.send_message(response)

@tree.command(name="find", description="Search for a drink by name.")
@app_commands.describe(name="The name to search for")
async def find(interaction: discord.Interaction, name: str):
    matches = process.extract(name, cocktails.keys(), limit=3)
    if not matches:
        await interaction.response.send_message("No drinks found.", ephemeral=True)
        return

    result = "Top matches:\n"
    for match, score in matches:
        drink = cocktails[match]
        result += f"**{drink['name']}** ({drink['rarity']}): {drink['recipe']}\n"
    await interaction.response.send_message(result)

@tree.command(name="setbar", description="Set the current channel as the bar channel.")
async def setbar(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need admin rights to use this command.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    if guild_id not in servers:
        servers[guild_id] = {}
    servers[guild_id]["bar_channel"] = str(interaction.channel.id)
    save_json("servers.json", servers)
    await interaction.response.send_message(f"{interaction.channel.mention} is now the bar channel.")

client.run(os.getenv("DISCORD_TOKEN"))
