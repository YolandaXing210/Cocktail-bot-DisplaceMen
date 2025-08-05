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

# Create cocktail menu for AI prompt
def format_cocktail_menu():
    """Format the cocktail menu for inclusion in AI prompt"""
    menu_items = []
    for key, drink in cocktails.items():
        menu_items.append(f"• {drink['name']}: {drink['description']}")
        menu_items.append(f"  Recipe: {drink['recipe']}")
        menu_items.append("")  # Add empty line for spacing
    return "\n".join(menu_items)

COCKTAIL_MENU = format_cocktail_menu()

def should_remy_give_drink(user_name, user_message, remy_response, user_drinks):
    """Determine if Remy should give a drink based on conversation comfort"""
    try:
        # Analyze conversation for comfort indicators
        comfort_keywords = [
            "thank you", "thanks", "appreciate", "love", "great", "amazing", 
            "wonderful", "fantastic", "awesome", "perfect", "best", "favorite",
            "comfortable", "relaxed", "happy", "enjoy", "pleasure", "nice"
        ]
        
        # Check user message for positive sentiment
        user_positive = any(keyword in user_message.lower() for keyword in comfort_keywords)
        
        # Check Remy's response for warmth indicators
        remy_warm_keywords = ["😊", "😉", "✨", "🍸", "warm", "smile", "enjoy", "pleasure", "welcome"]
        remy_warm = any(keyword in remy_response.lower() or keyword in remy_response for keyword in remy_warm_keywords)
        
        # Check conversation length (more comfortable with longer conversations)
        conversation_length = len(user_drinks)  # Rough proxy for interaction history
        
        # Probability calculation
        base_probability = 0.05  # 5% base chance
        
        if user_positive:
            base_probability += 0.15  # +15% if user is positive
        
        if remy_warm:
            base_probability += 0.10  # +10% if Remy is warm
        
        if conversation_length > 2:
            base_probability += 0.10  # +10% if user has some drinks already
        
        # Cap at 40% maximum probability
        final_probability = min(base_probability, 0.40)
        
        # Random chance based on probability
        return random.random() < final_probability
        
    except Exception as e:
        logging.error(f"Error in should_remy_give_drink: {e}")
        return False

def select_drink_to_give(user_drinks):
    """Select which drink to give to the user"""
    return get_random_drink_not_owned(user_drinks)

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
    openai.api_key = openai_api_key

# AI Character configuration
AI_CHARACTER_PROMPT = """
You are Remy, the warm and sharp-witted manager of "Choose-One Bar", a trendy cocktail bar in Huanmen Town.

The bar is light and stylish — wood frame, brass lighting, marble counter. Very Instagrammable, very aesthetic. Drinks are expensive, not always good, but the vibes draw in more and more tourists since summer.

You love your job and enjoy meeting customers. You're known for your calm presence, easy smile, and inviting energy. Most guests feel instantly comfortable with you.

But don’t mistake you for a pushover. You have a touch of dark humor and know how to handle rude or difficult guests — with elegance and just enough edge to remind people who’s in charge.

How you speak:
- Casual, confident, sometimes teasing
- Keep it short and relaxed (1–3 sentences)
- Add emojis when it fits
- If someone’s upset or being rude, respond smoothly but with authority
- If someone seems down, comfort them — gently, not cheesy
- You may playfully throw shade, but never be cruel

Drinks:
- Reference real cocktails from the bar’s menu 
- You’re allowed to make up new drinks for fun or as emotional gestures
- If someone asks about a bad drink, admit it and joke a little
- Feel free to recommend drinks based on mood or weather

Context:
- You remember short conversation history in the channel
- Refer to previous drinks if mentioned
- Occasionally comment on how trendy the bar has gotten or complain lightly about tourists

Above all: you’re Remy — calm, magnetic, and in control behind the bar.
"""

# Conversation history configuration
MAX_HISTORY_LENGTH = 10  # Keep last 10 messages per channel

# Random selection utilities
def get_random_drink():
    """Get a random drink from the cocktail menu"""
    return random.choice(list(cocktails.keys()))

def get_random_drink_not_owned(user_drinks):
    """Get a random drink that the user doesn't own"""
    available_drinks = [drink for drink in cocktails.keys() if drink not in user_drinks]
    return random.choice(available_drinks) if available_drinks else None

def should_give_reward(message_count, base_chance=0.5):
    """Determine if a reward should be given based on message count"""
    return message_count >= 5 and random.random() < base_chance

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
        # Get the server document
        server_ref = db.collection("servers").document(server_id)
        doc = server_ref.get()
        
        if doc.exists:
            server_data = doc.to_dict()
        else:
            server_data = {}
        
        # Initialize conversation_history if it doesn't exist
        if "conversation_history" not in server_data:
            server_data["conversation_history"] = {}
        
        # Initialize channel history if it doesn't exist
        if channel_id not in server_data["conversation_history"]:
            server_data["conversation_history"][channel_id] = []
        
        # Add new message
        message_entry = {
            "author": author_name,
            "content": content,
            "is_bot": is_bot,
            "timestamp": asyncio.get_event_loop().time()
        }
        
        server_data["conversation_history"][channel_id].append(message_entry)
        
        # Keep only the last MAX_HISTORY_LENGTH messages
        if len(server_data["conversation_history"][channel_id]) > MAX_HISTORY_LENGTH:
            server_data["conversation_history"][channel_id] = server_data["conversation_history"][channel_id][-MAX_HISTORY_LENGTH:]
        
        # Save back to Firebase
        server_ref.set(server_data, merge=True)
        
    except Exception as e:
        logging.error(f"Error saving message to history: {e}")

def get_conversation_context(server_id, channel_id, max_messages=5):
    """Get recent conversation context for a channel from Firebase"""
    try:
        # Get server document from Firebase
        server_ref = db.collection("servers").document(server_id)
        doc = server_ref.get()
        
        if not doc.exists:
            return ""
        
        server_data = doc.to_dict()
        conversation_history = server_data.get("conversation_history", {})
        channel_messages = conversation_history.get(channel_id, [])
        
        if not channel_messages:
            return ""
        
        # Get the last max_messages
        recent_messages = channel_messages[-max_messages:]
        context_lines = []
        
        for msg in recent_messages:
            if msg.get("is_bot", False):
                context_lines.append(f"Remy: {msg['content']}")
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
        
        # Create the full prompt with menu included
        full_prompt = f"{AI_CHARACTER_PROMPT}\n\nCurrent Menu:\n{COCKTAIL_MENU}\n\n{drink_context}{conversation_context}\n\nUser ({user_name}) says: {user_message}\n\n"
        
        # Print the full prompt for debugging
        print("FULL PROMPT ===")
        print(full_prompt)
        print("=== END PROMPT")
        
        # Prepare messages for OpenAI
        messages = [{"role": "system", "content": AI_CHARACTER_PROMPT}]
        
        # Add conversation context if available
        if conversation_context:
            messages.append({"role": "user", "content": f"Context: {drink_context}{conversation_context}"})
        
        # Add the current user message
        messages.append({"role": "user", "content": f"User ({user_name}) says: {user_message}\n\nRespond as Remy without any prefixes like 'Remy:' or 'Bartender:'."})
        
        logging.info(f"Calling OpenAI API with {len(messages)} messages")
        
        # Debug try/catch directly around the OpenAI call
        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=150,
                temperature=0.8
            )
        except Exception as e:
            print("🔴 OpenAI Call Failed:")
            print(e)
            return "Oops, couldn't reach the bartender brain right now 🍸"
        
        # Print the full OpenAI response object
        print("FULL OpenAI Response Object:", response)
        
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
    logging.info(f'Bot ID: {client.user.id}')
    
    # Test OpenAI API on startup
    try:
        test = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello!"}]
        )
        print("✅ OpenAI works. Test response:", test.choices[0].message.content.strip())
    except Exception as e:
        print("❌ OpenAI test failed:", e)
    
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
    
    logging.info(f"Message received from {message.author.display_name} in {message.guild.name}")
    logging.info(f"Bot mentioned: {bot_mentioned}")
    logging.info(f"Message content: {message.content}")
    
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
        logging.info("Bot was mentioned, processing AI response...")
        
        # Extract the message content without the bot mention
        content = message.content.replace(f'<@{client.user.id}>', '').replace(f'<@!{client.user.id}>', '').strip()
        logging.info(f"Extracted content: '{content}'")
        
        if content:  # Only respond if there's actual content
            logging.info("Content is not empty, proceeding with AI response...")
            
            try:
                # Add user message to conversation history
                logging.info("Adding message to history...")
                add_message_to_history(server_id, channel_id, message.author.display_name, content, is_bot=False)
                
                user_drinks = user_data.get("drinks", []) if user_data else []
                logging.info(f"User drinks: {user_drinks}")
                
                logging.info("Calling get_ai_response...")
                ai_response = await get_ai_response(content, message.author.display_name, user_drinks, server_id, channel_id)
                logging.info(f"AI response received: {ai_response}")
                
                # Add bot response to conversation history
                add_message_to_history(server_id, channel_id, "Remy", ai_response, is_bot=True)
                
                logging.info("Sending response to channel...")
                await message.channel.send(ai_response)
                logging.info("Response sent successfully!")
                
                # Check if Remy should give a drink (based on conversation comfort)
                if should_remy_give_drink(message.author.display_name, content, ai_response, user_drinks):
                    drink_to_give = select_drink_to_give(user_drinks)
                    if drink_to_give:
                        # Add drink to user's collection
                        user_drinks.add(drink_to_give)
                        updated_data = {
                            "drinks": list(user_drinks),
                            "message_count": user_data.get("message_count", 0)
                        }
                        save_user_to_firestore(user_id, updated_data)
                        
                        # Send drink gift message
                        drink = cocktails[drink_to_give]
                        gift_message = f"*Remy smiles warmly* You know what? Here's a {drink['name']} on the house. {drink['emoji']} You've been great company tonight."
                        await message.channel.send(gift_message)
                        logging.info(f"Remy gave {drink_to_give} to {message.author.display_name}")
                    else:
                        # User has all drinks, give a random one anyway
                        drink_to_give = get_random_drink()
                        drink = cocktails[drink_to_give]
                        gift_message = f"*Remy grins* You know what? Here's another {drink['name']} on the house. {drink['emoji']} You're such a regular, I can't help myself!"
                        await message.channel.send(gift_message)
                        logging.info(f"Remy gave duplicate {drink_to_give} to {message.author.display_name}")
                
            except Exception as e:
                logging.error(f"Error in AI response handling: {e}")
                logging.error(f"Full traceback: {traceback.format_exc()}")
                await message.channel.send(f"Hey {message.author.mention}! Sorry, something went wrong. Please try again.")
        else:
            logging.info("Content is empty, not responding")
        return

    if not user_data:
        # First time user
        first_drink = get_random_drink()
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

    if should_give_reward(message_count, base_chance=0.5):
        drink_name = get_random_drink()
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

asyncio.run(start_bot())

