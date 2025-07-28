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
import logging
logging.basicConfig(level=logging.INFO)
import asyncio
import openai


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

# OpenAI setup
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logging.error("OPENAI_API_KEY environment variable is not set!")
else:
    logging.info("OpenAI API key is configured")

# AI Character configuration
AI_CHARACTER_PROMPT = """You are a friendly and knowledgeable bartender at a cozy cocktail bar. You have a warm personality and love talking about drinks, cocktails, and creating a welcoming atmosphere. You're passionate about mixology and enjoy sharing your knowledge with customers.

Key traits:
- Warm, welcoming, and conversational
- Knowledgeable about cocktails and spirits
- Enjoys making people feel comfortable
- Has a sense of humor but keeps it appropriate
- Loves sharing drink recommendations and stories
- Speaks naturally, not like a formal assistant

When responding:
- Keep responses conversational and friendly
- You can mention specific cocktails from the bar's menu
- Be encouraging and supportive
- If asked about drinks you don't know, be honest but helpful
- Keep responses reasonably short (1-3 sentences typically)
- Use emojis occasionally to add personality
- Pay attention to the conversation context and refer to previous messages when relevant

Remember: You're a bartender, not a customer service bot. Be personable and engaging!"""

# Conversation history configuration
MAX_HISTORY_LENGTH = 10  # Keep last 10 messages per channel

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

def add_message_to_history(server_id, channel_id, author_name, content, is_bot=False):
    """Add a message to the conversation history for a channel in Firebase"""
    try:
        # Create a unique document ID for this channel's history
        history_ref = db.collection("servers").document(server_id).collection("conversation_history").document(channel_id)
        
        # Get current history
        doc = history_ref.get()
        if doc.exists:
            history_data = doc.to_dict()
            messages = history_data.get("messages", [])
        else:
            messages = []
        
        # Add new message
        message_entry = {
            "author": author_name,
            "content": content,
            "is_bot": is_bot,
            "timestamp": asyncio.get_event_loop().time()
        }
        
        messages.append(message_entry)
        
        # Keep only the last MAX_HISTORY_LENGTH messages
        if len(messages) > MAX_HISTORY_LENGTH:
            messages = messages[-MAX_HISTORY_LENGTH:]
        
        # Save back to Firebase
        history_ref.set({"messages": messages}, merge=True)
        
    except Exception as e:
        logging.error(f"Error saving message to history: {e}")

def get_conversation_context(server_id, channel_id, max_messages=5):
    """Get recent conversation context for a channel from Firebase"""
    try:
        # Get history from Firebase
        history_ref = db.collection("servers").document(server_id).collection("conversation_history").document(channel_id)
        doc = history_ref.get()
        
        if not doc.exists:
            return ""
        
        history_data = doc.to_dict()
        messages = history_data.get("messages", [])
        
        if not messages:
            return ""
        
        # Get the last max_messages
        recent_messages = messages[-max_messages:]
        context_lines = []
        
        for msg in recent_messages:
            if msg.get("is_bot", False):
                context_lines.append(f"Bartender: {msg['content']}")
            else:
                context_lines.append(f"{msg['author']}: {msg['content']}")
        
        return "\n".join(context_lines)
        
    except Exception as e:
        logging.error(f"Error getting conversation context: {e}")
        return ""

async def get_ai_response(user_message, user_name, user_drinks=None, server_id=None, channel_id=None):
    """Get AI response from OpenAI based on user message and context"""
    try:
        logging.info(f"Starting AI response for user: {user_name}, message: {user_message}")
        
        # Build context about the user's drink collection
        drink_context = ""
        if user_drinks:
            drink_names = [cocktails.get(drink, {}).get('name', drink) for drink in user_drinks if drink in cocktails]
            if drink_names:
                drink_context = f"\n\nContext: {user_name} has tried these drinks: {', '.join(drink_names)}."
        
        # Get conversation history context
        conversation_context = ""
        if server_id and channel_id:
            logging.info(f"Getting conversation context for server: {server_id}, channel: {channel_id}")
            conversation_context = get_conversation_context(server_id, channel_id, max_messages=5)
            if conversation_context:
                conversation_context = f"\n\nRecent conversation:\n{conversation_context}"
        
        # Create the full prompt
        full_prompt = f"{AI_CHARACTER_PROMPT}{drink_context}{conversation_context}\n\nUser ({user_name}) says: {user_message}\n\nBartender:"
        
        # Prepare messages for OpenAI
        messages = [{"role": "system", "content": AI_CHARACTER_PROMPT}]
        
        # Add conversation context if available
        if conversation_context:
            messages.append({"role": "user", "content": f"Context: {drink_context}{conversation_context}"})
        
        # Add the current user message
        messages.append({"role": "user", "content": f"User ({user_name}) says: {user_message}"})
        
        logging.info(f"Calling OpenAI API with {len(messages)} messages")
        
        # Get response from OpenAI
        client = openai.OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=150,
            temperature=0.8
        )
        
        logging.info(f"OpenAI response received successfully")
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        logging.error(f"Error getting AI response: {e}")
        logging.error(f"Full traceback: {traceback.format_exc()}")
        return f"Hey {user_name}! Sorry, I'm having trouble thinking straight right now. Maybe it's the late shift catching up to me! 😅"

# Discord setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

@client.event
async def on_ready():
    logging.info(f'Bot is ready as {client.user}')
    try:
        synced = await tree.sync()
        logging.info(f'Synced {len(synced)} global commands')
    except Exception as e:
        logging.error(f'Failed to sync commands globally: {e}')
        
        
@client.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    server_id = str(message.guild.id)
    channel_id = str(message.channel.id)

    # Check if bot is mentioned (for AI responses)
    bot_mentioned = client.user in message.mentions
    
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

    # Handle AI responses if bot is mentioned
    if bot_mentioned:
        # Extract the message content without the bot mention
        content = message.content.replace(f'<@{client.user.id}>', '').replace(f'<@!{client.user.id}>', '').strip()
        
        if content:  # Only respond if there's actual content
            # Add user message to conversation history
            add_message_to_history(server_id, channel_id, message.author.display_name, content, is_bot=False)
            
            user_drinks = user_data.get("drinks", []) if user_data else []
            ai_response = await get_ai_response(content, message.author.display_name, user_drinks, server_id, channel_id)
            
            # Add bot response to conversation history
            add_message_to_history(server_id, channel_id, "Bartender", ai_response, is_bot=True)
            
            await message.channel.send(ai_response)
        return

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

    # Add regular message to conversation history (for context)
    add_message_to_history(server_id, channel_id, message.author.display_name, message.content, is_bot=False)
    
    # Save updates
    updated_data = {
        "drinks": list(drinks),
        "message_count": message_count
    }
    save_user_to_firestore(user_id, updated_data)

@client.event
async def on_disconnect():
    logging.warning("Bot disconnected from Discord.")

@client.event
async def on_resumed():
    logging.info("Bot reconnected to Discord.")

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

@tree.command(name="speakremy", description="Make the bot say something.")
@app_commands.describe(message="The bot says...")
async def speakremy(interaction: discord.Interaction, message: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("You’re not allowed to use this command.", ephemeral=True)
        return

    # Don't show any response to the user
    await interaction.response.defer(thinking=False, ephemeral=True)

    # Send as bot message
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


@tree.command(name="give", description="Give a specific cocktail to a user. (Owner only)")
@app_commands.describe(user="The user to give the cocktail to", cocktail="The name of the cocktail to give")
async def give(interaction: discord.Interaction, user: discord.Member, cocktail: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("You're not allowed to use this command.", ephemeral=True)
        return

    try:
        # Find the cocktail using fuzzy matching
        matches = process.extract(cocktail, cocktails.keys(), limit=1)
        
        if not matches:
            await interaction.response.send_message("No cocktail found with that name. Try again or check your spelling.", ephemeral=True)
            return

        best_match, score = matches[0]
        
        # Get the user's current data
        user_id = str(user.id)
        user_data = get_user_from_firestore(user_id)
        
        # Handle case where user_data might be None (though get_user_from_firestore should handle this)
        if user_data is None:
            user_data = {"drinks": [], "message_count": 0}
        
        user_drinks = set(user_data.get("drinks", []))
        
        # Add the cocktail to user's collection
        user_drinks.add(best_match)
        
        # Save updated data
        updated_data = {
            "drinks": list(user_drinks),
            "message_count": user_data.get("message_count", 0)
        }
        save_user_to_firestore(user_id, updated_data)
        
        # Send confirmation message
        drink = cocktails[best_match]
        await interaction.response.defer(thinking=False, ephemeral=True)
        await interaction.channel.send(
            f"{user.mention}, here is the **{drink['name']}** for you. {drink['emoji']}"
        )
        
    except Exception as e:
        logging.error(f"Error in give command: {e}")
        await interaction.response.send_message(
            f"❌ Failed to give cocktail to {user.mention}. Please try again or check the logs.", 
            ephemeral=True
        )

async def start_bot():
    while True:
        try:
            await client.start(os.getenv("DISCORD_TOKEN"))
        except Exception as e:
            print("Bot crashed. Restarting in 5 seconds...", e)
            await asyncio.sleep(5)

async def bot_watchdog():
    while True:
        if client.is_closed():
            logging.warning("Bot is closed. Attempting to restart...")
            try:
                await client.start(os.getenv("DISCORD_TOKEN"))
            except Exception as e:
                logging.error("Restart attempt failed:\n" + traceback.format_exc())
        await asyncio.sleep(300)  # check every 5 minutes

async def run_bot_forever():
    while True:
        try:
            logging.info("Starting bot...")
            await client.start(os.getenv("DISCORD_TOKEN"))
        except Exception as e:
            logging.error("Bot crashed. Restarting in 5 seconds...\n" + traceback.format_exc())
            await asyncio.sleep(5)
        else:
            logging.warning("Bot stopped cleanly. Restarting in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot_forever())
    except KeyboardInterrupt:
        logging.info("Shutdown requested by user.")

