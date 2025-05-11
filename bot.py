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
            users[user_id] = {"drinks": [], "message_count": 0}
            first_drink = random.choice(list(cocktails.keys()))
            users[user_id]["drinks"].append(first_drink)
            await message.channel.send(f"Welcome to the bar, {message.author.mention}. Take a seat and relax. Here's your first drink on the house: {cocktails[first_drink]['name']} 🍸")
            save_json("users.json", users)
            return

        users[user_id]["message_count"] += 1
        if users[user_id]["message_count"] >= 5:
           if random.random() < 0.5:
                drink_name = random.choice(list(cocktails.keys()))
                if drink_name not in users[user_id]["drinks"]:
                    users[user_id]["drinks"].append(drink_name)
                await message.channel.send(f"{message.author.mention}, here is your new drink: {cocktails[drink_name]['name']}. 🥂 Keep the conversation going.")
                users[user_id]["message_count"] = 0
        save_json("users.json", users)

@tree.command(name="inventory", description="View your drink collection.")
async def inventory(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)

    user_id = str(interaction.user.id)
    user_data = users.get(user_id, {"drinks": []})
    drinks = user_data["drinks"]
    total = len(cocktails)
    drink_names = [cocktails[d]["name"] for d in drinks if d in cocktails]
    message = f"You own {len(drinks)}/{total} drinks:\n" + "\n".join(drink_names) if drink_names else "You have no drinks yet."
    await interaction.followup.send(message)

@tree.command(name="find", description="Search for a drink you own by name.")
@app_commands.describe(name="The name to search for")
async def find(interaction: discord.Interaction, name: str):
    user_id = str(interaction.user.id)
    user_drinks = set(users.get(user_id, {}).get("drinks", []))

    # Use fuzzy matching to find potential drinks by name
    matches = process.extract(name, cocktails.keys(), limit=3)
    if not matches:
        await interaction.response.send_message("No drinks found. Try again or check your spelling.", ephemeral=True)
        return

    result = ""
    found = False
    # Iterate through the matches
    for match, score in matches:
        # Check if the matched drink is owned by the user
        if match in user_drinks:
            drink = cocktails[match]
            result += f"**{drink['name']}**\n({drink.get('description', 'No description')})\n{drink.get('recipe', 'No recipe')}\n{drink.get('image', '')}\n\n"
            found = True

    if found:
        # If we found at least one drink owned by the user
        await interaction.response.send_message(result, ephemeral=True)
    else:
        # No matching drink found in user's collection
        await interaction.response.send_message("You don't have that drink yet. Try again or check your spelling.", ephemeral=True)


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

@tree.command(name="deletebar", description="Remove the bar channel setting for this server.")
async def deletebar(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need admin rights to use this command.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    if guild_id in servers and "bar_channel" in servers[guild_id]:
        del servers[guild_id]["bar_channel"]
        save_json("servers.json", servers)
        await interaction.response.send_message("Bar channel has been unset.")
    else:
        await interaction.response.send_message("No bar channel was set for this server.", ephemeral=True)


client.run(os.getenv("DISCORD_TOKEN"))
