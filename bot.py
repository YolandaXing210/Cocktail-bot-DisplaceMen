import discord
from discord import app_commands
import json
import random
import os
from fuzzywuzzy import process
from flask import Flask
from threading import Thread
import firebase_admin
from firebase_admin import credentials, firestore
import traceback

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

# Load Firebase credentials from environment variables
cred = credentials.Certificate({
    "type": "service_account",
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace('\\n', '\n'),  # Ensure correct newline handling
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "client_id": os.getenv("FIREBASE_CLIENT_ID"),
    "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
    "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
    "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_X509_CERT_URL"),
    "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_X509_CERT_URL"),
    "universe_domain": "googleapis.com"
})

try:
    firebase_admin.initialize_app(cred)
    print("Firebase initialized")
except Exception as e:
    print("Firebase init failed:", e)

# Get Firestore instance

try:
    db = firestore.client()
    print("Firebase client")
except Exception as e:
    print("Firebase client failed:", e)

OWNER_ID = int(os.getenv("OWNER_ID"))

def get_user_from_firestore(user_id):
    # Access the "users" collection and get the user's data by user ID
    user_ref = db.collection("users").document(user_id)
    doc = user_ref.get()
    return doc.to_dict() if doc.exists else {"drinks": [], "message_count": 0}

def save_user_to_firestore(user_id, user_data):
    # Save the user data back to Firestore, merging with the existing document
    user_ref = db.collection("users").document(user_id)
    user_ref.set(user_data, merge=True)

# Discord setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    print(f'Bot is ready as {client.user}')
    for guild in client.guilds:
        try:
            synced = await tree.sync(guild=guild)
            print(f'Synced {len(synced)} commands in {guild.name}')
        except Exception as e:
            print(f'Failed to sync commands in {guild.name}: {e}')

@client.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    server_id = str(message.guild.id)
    channel_id = str(message.channel.id)

    # 🔽 Query Firestore to get the bar channel for this server
    server_ref = db.collection("servers").document(server_id)
    server_doc = server_ref.get()
    if not server_doc.exists:
        return

    server_data = server_doc.to_dict()
    if "bar_channel" not in server_data or server_data["bar_channel"] != channel_id:
        return

    user_id = str(message.author.id)
    user_data = get_user_from_firestore(user_id)

    if not user_data:
        # First time user
        first_drink = random.choice(list(cocktails.keys()))
        new_data = {
            "drinks": [first_drink],
            "message_count": 0
        }
        await message.channel.send(
            f"Welcome to the bar, {message.author.mention}. "
            f"Take a seat and relax. Here's your first drink on the house: {cocktails[first_drink]['name']} {cocktails[first_drink]['emoji']}"
        )
        
        save_user_to_firestore(user_id, new_data)
        return

    # Returning user
    drinks = set(user_data.get("drinks", []))
    message_count = user_data.get("message_count", 0) + 1

    if message_count >= 5:
        if random.random() < 0.5:
            drink_name = random.choice(list(cocktails.keys()))
            if drink_name not in drinks:
                drinks.add(drink_name)
            await message.channel.send(
                f"{message.author.mention}, here is your new drink: "
                f"{cocktails[drink_name]['name']} {cocktails[drink_name]['emoji']}. Keep the conversation going."
            )
            message_count = 0  # Reset after reward

    # Save updates
    updated_data = {
        "drinks": list(drinks),
        "message_count": message_count
    }
    save_user_to_firestore(user_id, updated_data)



@tree.command(name="inventory", description="View your drink collection.")
async def inventory(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)

    user_id = str(interaction.user.id)
    user_data = get_user_from_firestore(user_id)
    drinks = user_data.get("drinks", [])
    total = len(cocktails)
    drink_names = [cocktails[d]["name"] for d in drinks if d in cocktails]

    if drink_names:
        message = f"You own {len(drinks)}/{total} drinks:\n" + "\n".join(drink_names)
    else:
        message = "You have no drinks yet."

    await interaction.followup.send(message)

@tree.command(name="speak", description="Make the bot say something.")
@app_commands.describe(name="The bot says...")
async def speak(interaction: discord.Interaction, message: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("You’re not allowed to use this command.", ephemeral=True)
        return

    await interaction.response.defer()
    await interaction.channel.send(message)

@tree.command(name="find", description="Search for a drink you own by name.")
@app_commands.describe(name="The name to search for")
async def find(interaction: discord.Interaction, name: str):
    user_id = str(interaction.user.id)
    user_data = get_user_from_firestore(user_id)
    user_drinks = set(user_data.get("drinks", []))

    matches = process.extract(name, cocktails.keys(), limit=1)

    if not matches:
        await interaction.response.send_message("No drinks found. Try again or check your spelling.", ephemeral=True)
        return

    best_match = None
    for match, score in matches:
        if match in user_drinks:
            best_match = match
            break

    if not best_match:
        await interaction.response.send_message("You don't have that drink yet.", ephemeral=True)
        return

    drink = cocktails[best_match]
    result = f"**{drink['name']}**\n"
    result += f"({drink.get('description', 'No description')})\n"
    result += f"{drink.get('recipe', 'No recipe')}\n"
    result += f"{drink.get('image', '')}"

    await interaction.response.send_message(result, ephemeral=True)
    matches.clear()


@tree.command(name="setbar", description="Set the current channel as the bar channel.")
async def setbar(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need admin rights to use this command.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    bar_channel_id = str(interaction.channel.id)

    # Save to Firestore
    db.collection("servers").document(guild_id).set({"bar_channel": bar_channel_id}, merge=True)

    await interaction.response.send_message(f"{interaction.channel.mention} is now the bar channel.")


@tree.command(name="deletebar", description="Remove the bar channel setting for this server.")
async def deletebar(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You need admin rights to use this command.", ephemeral=True)
        return

    guild_id = str(interaction.guild.id)
    server_ref = db.collection("servers").document(guild_id)
    server_data = server_ref.get()

    if server_data.exists and "bar_channel" in server_data.to_dict():
        server_ref.update({"bar_channel": firestore.DELETE_FIELD})
        await interaction.response.send_message("Bar channel has been unset.")
    else:
        await interaction.response.send_message("No bar channel was set for this server.", ephemeral=True)


try:
    client.run(os.getenv("DISCORD_TOKEN"))
except Exception as e:
    print("Failed to start bot:", e)
    traceback.print_exc()

client.run(os.getenv("DISCORD_TOKEN"))
print("DISCORD_TOKEN:", os.getenv("DISCORD_TOKEN")[:10])  # Don't print the full token

