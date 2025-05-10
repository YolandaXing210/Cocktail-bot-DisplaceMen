import discord
from discord.ext import commands
import json
import random
import os
from fuzzywuzzy import process
from flask import Flask
from threading import Thread

TOKEN = os.environ.get('DISCORD_TOKEN')

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

# Load data from JSON files
def load_json(file_path):
    with open(file_path, 'r') as f:
        return json.load(f)


def save_json(file_path, data):
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)


# Load data files
cocktails = load_json('drinks.json')
users = load_json('users.json')
servers = load_json('servers.json')


# Function to choose a drink based on weighted distribution
def choose_random_drink(cocktails):
    return random.choice(list(cocktails.values()))


# Intents and bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
bot = commands.Bot(command_prefix='/', intents=intents)


@bot.event
async def on_ready():
    print(f'Bot is ready as {bot.user}')


# Command to set the bar channel (admin only)
@bot.command(name="setbar", description="Set the bar channel for the server")
@commands.has_permissions(administrator=True)
async def set_bar(ctx):
    servers[str(ctx.guild.id)] = {"bar_channel": str(ctx.channel.id)}
    save_json('servers.json', servers)
    await ctx.send(
        "🍸 **Welcome to the Bar.**\n"
        "This channel has been set as the bar. Drop a message and you might get a drink.\n"
        "I’ll be quietly watching. Speak up, and I’ll serve you something—randomly graded, sometimes rare.\n"
        "Please DM the me if you wanna check your `/inventory` to see what you’ve collected.\n"
        "Your first one's on the house.")


# Command to delete the bar channel (admin only)
@bot.command(name="deletebar",
             description="Remove the bar channel for the server")
@commands.has_permissions(administrator=True)
async def delete_bar(ctx):
    if str(ctx.guild.id) in servers:
        del servers[str(ctx.guild.id)]
        save_json('servers.json', servers)
        await ctx.send("Bar channel removed.")
    else:
        await ctx.send("No bar channel set for this server.")


# Handle first message in #bar
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Ensure the message is from a server (not a DM)
    if message.guild is not None:
        # Check if message is in bar channel
        bar_channel_id = servers.get(str(message.guild.id),
                                     {}).get("bar_channel")
        if bar_channel_id and str(message.channel.id) == bar_channel_id:
            user_id = str(message.author.id)

            # If the user doesn't have a drink yet, assign the first drink
            if user_id not in users:
                users[user_id] = {"drinks": []}
                # Filter cocktails to only include 1-star drinks
                drink = choose_random_drink(cocktails)
                users[user_id]["drinks"].append(
                    drink['name'])  # Store only the drink's name
                save_json('users.json', users)

                # Send a public message
                await message.channel.send(
                    f"🍸 Looks like you're new to the bar. Welcome. You've got a [{drink['name']}] on the house. Take a seat and relax."
                )

                # Send a DM to the user
                await message.author.send(
                    "Use DMs for bot commands. Try /inventory to check your drinks."
                )

            # Message-based rewards (after 5+ messages)
            else:
                user_data = users[user_id]
                user_data["message_count"] = user_data.get("message_count",
                                                           0) + 1
                if user_data["message_count"] >= 7:
                    # 50% chance to get a new drink
                    if random.random() < 0.5:
                        # Assign a new drink (random rarity)
                        new_drink = random.choice(list(cocktails.values()))

                        # Send a public message no matter what
                        await message.channel.send(
                            f"🍸 Another round's on you. Here's a [{new_drink['name']}]. Keep the conversation flowing."
                        )

                        # Only add to collection if they don't already have it
                        if new_drink['name'] not in user_data["drinks"]:
                            user_data["drinks"].append(new_drink['name'])

                        # Reset message count
                        user_data["message_count"] = 0
                        save_json('users.json', users)
    await bot.process_commands(message)


# Inventory command (DM only)
@bot.command(name="inventory", description="View your drink collection.")
async def inventory(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        user_data = users.get(str(ctx.author.id), {"drinks": []})
        drinks_list = []

        for drink_name in user_data["drinks"]:
            # Get the drink details from drinks.json based on the stored name
            drink = cocktails.get(drink_name)
            if drink:
                drinks_list.append(f"{drink['name']}")

        total_drinks = len(cocktails)

        await ctx.send(
            f"Here's your collection ({len(user_data['drinks'])}/{total_drinks}):\n"
            + "\n".join(drinks_list))
    else:
        # Send a private message if the user used the command in the public channel
        await ctx.author.send("Please use DMs for this command.")
        # Delete the command message from the public channel to keep it clean
        await ctx.message.delete()


@bot.command(name="cocktail",
             description="View info about a specific cocktail.")
async def cocktail(ctx, *, cocktail_name: str):
    if isinstance(ctx.channel, discord.DMChannel):
        user_data = users.get(str(ctx.author.id), {"drinks": []})

        # Find the closest match using fuzzy matching
        drink_names = list(cocktails.keys())
        closest_match, score = process.extractOne(cocktail_name, drink_names)

        if score >= 80:  # Set a reasonable threshold for fuzzy match
            drink = cocktails.get(closest_match)
            if closest_match in user_data["drinks"]:
                await ctx.send(
                    f"{drink['name']}\nDescription: {drink['description']}\nRecipe: {drink['recipe']}\n{drink['image']}"
                )
            else:
                await ctx.send(
                    f"You don't have {drink['name']} yet. Try earning it!")
        else:
            await ctx.send(
                f"Sorry, I couldn't find a cocktail with the name {cocktail_name}. Please check the spelling or try again."
            )
    else:
        # Send a private message if the user used the command in the public channel
        await ctx.author.send("Please use DMs for this command.")
        # Delete the command message from the public channel to keep it clean
        await ctx.message.delete()


@bot.command(name="helpme", description="List all available commands.")
async def helpme(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("Here's what you can do:\n"
                       "/inventory – See your collected drinks\n"
                       "/cocktail [cocktail name] – View info about a drink")
    else:
        # Send a private message if the user used the command in the public channel
        await ctx.author.send(
            "Please use DMs for commands to keep the channel clean. Here's the help guide:\n"
            "/inventory – See your collected drinks\n"
            "/cocktail [cocktail name] – View info about a drink")
        # Delete the command message from the public channel to keep it clean
        await ctx.message.delete()


# Run the bot
bot.run(TOKEN)
