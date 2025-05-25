import discord
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, time, timedelta # time ถูก import แต่ไม่ได้ใช้โดยตรง อาจลบออกได้ถ้าไม่จำเป็น
import pytz
import traceback

# --- START: Keep Alive Web Server Dependencies ---
from flask import Flask
from threading import Thread
# --- END: Keep Alive Web Server Dependencies ---

# --- START: dotenv for local environment variables ---
from dotenv import load_dotenv
load_dotenv() # โหลดค่าจาก .env เข้า environment variables
# --- END: dotenv ---

# --- Bot Configuration ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix='$$', intents=intents)
TZ_BANGKOK = pytz.timezone('Asia/Bangkok')

# --- Inventory System Variables ---
AVAILABLE_ITEMS = ["ไม้", "หิน", "เหล็ก", "ยา", "กระสุน", "ผ้า", "อาหารหลัก", "ชุดเกราะ", "ปืนไรเฟิล", "ระเบิดมือ", "กับดัก", "อุปกรณ์ซ่อม"]
AVAILABLE_ITEMS.sort()
LEADER_ROLES = ["หัวหน้าแก๊ง", "รองหัวหน้า", "Officer", "แกนนำ"] # ตรวจสอบว่าชื่อ Role ตรงกับใน Discord Server

TEAM_INVENTORY_FILE = 'team_inventory_dedicated.json'
TEAM_BANK_FILE = 'team_bank.json'

CONTROL_PANEL_CHANNEL_ID = 1375820779350003712  # <<-- ตรวจสอบว่า ID นี้ถูกต้อง และบอทมีสิทธิ์ในห้องนี้
CONTROL_PANEL_MESSAGE_ID_FILE = 'control_panel_message_id.txt'

# --- Data Structures ---
team_inventory = {item: 0 for item in AVAILABLE_ITEMS}
team_bank = {"balance": 0, "log": []}

# --- Helper Functions ---
def log_ts():
    return f"[{datetime.now(TZ_BANGKOK).strftime('%Y-%m-%d %H:%M:%S %Z')}]"

def load_data():
    global team_inventory, team_bank
    # Inventory
    try:
        with open(TEAM_INVENTORY_FILE, 'r', encoding='utf-8') as f:
            loaded_data = json.load(f)
        # Ensure all AVAILABLE_ITEMS are present, initialize new items to 0
        temp_inventory = {item: 0 for item in AVAILABLE_ITEMS}
        temp_inventory.update({k: v for k, v in loaded_data.items() if k in AVAILABLE_ITEMS}) # Only load known items
        team_inventory = temp_inventory
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"{log_ts()} WARNING: {TEAM_INVENTORY_FILE} not found or invalid ({e}). Initializing.")
        team_inventory = {item: 0 for item in AVAILABLE_ITEMS}
    # Bank
    try:
        with open(TEAM_BANK_FILE, 'r', encoding='utf-8') as f:
            team_bank = json.load(f)
            if "balance" not in team_bank: team_bank["balance"] = 0
            if "log" not in team_bank: team_bank["log"] = []
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"{log_ts()} WARNING: {TEAM_BANK_FILE} not found or invalid ({e}). Initializing.")
        team_bank = {"balance": 0, "log": []}
    # No need for finally save here, save when changes occur

def save_inventory_to_file():
    try:
        with open(TEAM_INVENTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(team_inventory, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"{log_ts()} ERROR saving inventory: {e}")

def save_bank_data():
    try:
        with open(TEAM_BANK_FILE, 'w', encoding='utf-8') as f:
            json.dump(team_bank, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"{log_ts()} ERROR saving bank data: {e}")

async def update_inventory_action(item_name: str, quantity_change: int, action: str):
    if item_name not in AVAILABLE_ITEMS: # Check against defined items
        print(f"{log_ts()} Attempted action on unknown item: {item_name}")
        return False # Or handle as an error appropriate for your logic

    current_quantity = team_inventory.get(item_name, 0) # Use .get for safety

    if action == "deposit":
        team_inventory[item_name] = current_quantity + quantity_change
    elif action == "withdraw":
        if current_quantity < quantity_change:
            return False # Not enough items
        team_inventory[item_name] = current_quantity - quantity_change
    else:
        return False # Unknown action
    save_inventory_to_file()
    return True

async def send_item_log(target_channel_obj: discord.TextChannel, item_name: str, quantity: int, action: str, success: bool, reason: str, user: discord.User):
    if not target_channel_obj:
        print(f"{log_ts()} ERROR: Item log target_channel_obj is None. User: {user.name}, Item: {item_name}")
        return

    action_thai = "ฝาก" if action == "deposit" else "เบิก"
    title_emoji, color = ("✅", discord.Color.green()) if success else ("⚠️", discord.Color.orange())
    title = f"{title_emoji} {action_thai}ของ: {item_name}"
    embed = discord.Embed(title=title, color=color)

    current_item_amount = team_inventory.get(item_name, 0) # Get current amount for log

    if success:
        embed.description = f"{user.mention} ได้{action_thai} **{item_name}** จำนวน **{quantity}** ชิ้น"
        embed.add_field(name="คงเหลือในคลัง", value=f"**{item_name}**: {current_item_amount} ชิ้น", inline=False)
    else:
        embed.description = f"{user.mention} พยายาม{action_thai} **{item_name}** จำนวน **{quantity}** ชิ้น แต่มีไม่พอ (มี {current_item_amount} ชิ้น)"

    if reason:
        embed.add_field(name="เหตุผล", value=reason, inline=False)
    embed.set_footer(text=f"โดย: {user.display_name}")
    embed.timestamp = datetime.now(TZ_BANGKOK)
    try:
        await target_channel_obj.send(embed=embed)
    except Exception as e:
        print(f"{log_ts()} ERROR sending item log: {e}")


async def update_bank_action(amount: int, action: str, user: discord.User, reason: str):
    global team_bank
    balance_before = team_bank["balance"]
    if action == "deposit":
        team_bank["balance"] += amount
    elif action == "withdraw":
        if team_bank["balance"] < amount:
            return False
        team_bank["balance"] -= amount
    else:
        return False
    log_entry = {
        "timestamp": datetime.now(TZ_BANGKOK).isoformat(),
        "user_id": user.id,
        "user_name": user.name,
        "action": action,
        "amount": amount,
        "reason": reason,
        "balance_before": balance_before,
        "balance_after": team_bank["balance"]
    }
    team_bank["log"].append(log_entry)
    team_bank["log"] = team_bank["log"][-100:] # Keep only last 100 logs
    save_bank_data()
    return True

async def send_bank_log(target_channel_obj: discord.TextChannel, amount: int, action: str, success: bool, reason: str, user: discord.User):
    if not target_channel_obj:
        print(f"{log_ts()} ERROR: Bank log target_channel_obj is None. User: {user.name}, Amount: {amount}")
        return

    action_thai = "ฝาก" if action == "deposit" else "ถอน"
    title_emoji, color = ("✅", discord.Color.green()) if success else ("⚠️", discord.Color.red())
    title = f"{title_emoji} {action_thai}เงิน"
    embed = discord.Embed(title=title, color=color)
    if success:
        embed.description = f"{user.mention} ได้{action_thai}เงิน **{amount:,}** บาท"
        embed.add_field(name="ยอดคงเหลือใหม่", value=f"**{team_bank['balance']:,}** บาท", inline=False)
    else:
        embed.description = f"{user.mention} พยายาม{action_thai}เงิน **{amount:,}** บาท แต่มีไม่พอ (มี {team_bank['balance']:,} บาท)"
    if reason:
        embed.add_field(name="เหตุผล", value=reason, inline=False)
    embed.set_footer(text=f"โดย: {user.display_name}")
    embed.timestamp = datetime.now(TZ_BANGKOK)
    try:
        await target_channel_obj.send(embed=embed)
    except Exception as e:
        print(f"{log_ts()} ERROR sending bank log: {e}")

# --- UI Classes ---
# (QuantityReasonModal, ItemSelectForTransaction, EphemeralItemSelectView, BankTransactionModal, PersistentInventoryView - โค้ดเหมือนเดิม แนะนำให้ตรวจสอบ logic การอนุญาต)
# ... (โค้ด UI Classes ของคุณ) ...
# ตรวจสอบใน Modal และ Button handlers ว่ามีการเช็คสิทธิ์ (LEADER_ROLES) อย่างถูกต้องและครอบคลุม
# เช่น ใน PersistentInventoryView._handle_item_action ถ้า action_type == "withdraw" ควรเช็คสิทธิ์

class QuantityReasonModal(discord.ui.Modal):
    def __init__(self, item_name: str, action_type: str, title: str, original_channel: discord.TextChannel):
        super().__init__(title=title, timeout=180)
        self.item_name, self.action_type, self.original_channel = item_name, action_type, original_channel
        self.quantity_input = discord.ui.TextInput(label="จำนวน", placeholder="ตัวเลข", required=True, style=discord.TextStyle.short)
        self.add_item(self.quantity_input)
        self.reason_input = discord.ui.TextInput(label="เหตุผล (ไม่บังคับถ้าฝาก)", placeholder="...", required=(action_type == "withdraw"), style=discord.TextStyle.long, max_length=200)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.quantity_input.value)
            if quantity <= 0: raise ValueError("Quantity must be positive")
        except ValueError:
            await interaction.response.send_message("⚠️ จำนวนไม่ถูกต้อง (ต้องเป็นตัวเลขมากกว่า 0)", ephemeral=True); return

        reason = self.reason_input.value
        if self.action_type == "withdraw" and not reason: # บังคับเหตุผลถ้าเบิก
            await interaction.response.send_message("⚠️ กรุณาระบุเหตุผลในการเบิก", ephemeral=True); return

        if self.action_type == "withdraw" and not any(role.name in LEADER_ROLES for role in interaction.user.roles):
            await interaction.response.send_message(f"🚫 ไม่มีสิทธิ์เบิกของ! (ต้องมี Role: {', '.join(LEADER_ROLES)})", ephemeral=True); return

        await interaction.response.defer(ephemeral=True, thinking=True)
        success = await update_inventory_action(self.item_name, quantity, self.action_type)
        await send_item_log(self.original_channel, self.item_name, quantity, self.action_type, success, reason or "N/A", interaction.user)
        await interaction.followup.send(f"ทำรายการสำเร็จ!" if success else "ทำรายการไม่สำเร็จ (ของอาจไม่พอ หรือชื่อไอเทมผิด)", ephemeral=True)
        await setup_inventory_control_panel() # Make sure this function is robust

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"{log_ts()} Error in QuantityReasonModal: {error}"); traceback.print_exc()
        try:
            if not interaction.response.is_done(): await interaction.response.send_message("เกิดข้อผิดพลาดในการดำเนินการ Modal", ephemeral=True)
            else: await interaction.followup.send("เกิดข้อผิดพลาดในการดำเนินการ Modal", ephemeral=True)
        except Exception as e_resp: print(f"{log_ts()} Error sending error response in QRModal: {e_resp}")


class ItemSelectForTransaction(discord.ui.Select):
    def __init__(self, action_type: str, items_list: list, original_channel: discord.TextChannel):
        self.action_type, self.original_channel = action_type, original_channel
        # Ensure items_list contains only valid item names
        valid_items = [item for item in items_list if item in AVAILABLE_ITEMS]
        options = [discord.SelectOption(label=item) for item in valid_items[:25]] # Max 25 options
        if not options:
            options = [discord.SelectOption(label="ไม่มีไอเทมให้เลือก", value="_NO_ITEMS_", description="อาจจะยังไม่มีของในคลัง (ถ้าเบิก)")]
        super().__init__(placeholder="เลือกไอเทม...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        selected_item = self.values[0]
        if selected_item == "_NO_ITEMS_":
            await interaction.response.edit_message(content="ไม่มีไอเทมให้เลือกในขณะนี้", view=None); return

        verb = "ฝาก" if self.action_type == "deposit" else "เบิก"
        modal = QuantityReasonModal(selected_item, self.action_type, f"{verb} {selected_item}", self.original_channel)
        await interaction.response.send_modal(modal)
        try:
            # Edit the original ephemeral message that sent the select menu
            await interaction.edit_message(content=f"กำลังดำเนินการกับ **{selected_item}**... กรุณากรอกข้อมูลในหน้าต่างที่เด้งขึ้นมา", view=None)
        except discord.NotFound:
            print(f"{log_ts()} WARN: Ephemeral msg for item select might have been dismissed by user or timed out before modal submission.")
        except Exception as e:
            print(f"{log_ts()} Error editing item select message: {e}")


class EphemeralItemSelectView(discord.ui.View):
    def __init__(self, action_type: str, items_list: list, author_id: int, original_channel: discord.TextChannel):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.message = None # Store the message this view is attached to
        self.add_item(ItemSelectForTransaction(action_type, items_list, original_channel))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("คุณไม่ใช่ผู้ริเริ่มคำสั่งนี้", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message: # If we have a reference to the message
            try:
                await self.message.edit(content="หมดเวลาเลือกไอเทมแล้ว", view=None)
            except discord.NotFound:
                print(f"{log_ts()} EphemeralItemSelectView: Message already deleted or not found on timeout.")
            except Exception as e:
                print(f"{log_ts()} Error on EphemeralItemSelectView timeout trying to edit message: {e}")
        # For truly ephemeral messages, there might not be a message to edit if interaction.response.send_message was used
        # with ephemeral=True directly for the view. Components will just disable.


class BankTransactionModal(discord.ui.Modal):
    def __init__(self, action_type: str, title: str, original_channel: discord.TextChannel):
        super().__init__(title=title, timeout=180)
        self.action_type, self.original_channel = action_type, original_channel
        self.amount_input = discord.ui.TextInput(label="จำนวนเงิน", placeholder="ตัวเลข", required=True, style=discord.TextStyle.short)
        self.add_item(self.amount_input)
        self.reason_input = discord.ui.TextInput(label="เหตุผล", placeholder="ระบุเหตุผล", required=True, style=discord.TextStyle.long, max_length=200)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.amount_input.value)
            if amount <= 0: raise ValueError("Amount must be positive")
        except ValueError:
            await interaction.response.send_message("⚠️ จำนวนเงินไม่ถูกต้อง (ต้องเป็นตัวเลขมากกว่า 0)", ephemeral=True); return

        reason = self.reason_input.value # Reason is always required for bank transactions as per your UI
        if not reason: # Should not happen if TextInput is required=True, but good to double check.
             await interaction.response.send_message("⚠️ กรุณาระบุเหตุผล", ephemeral=True); return


        if self.action_type == "withdraw" and not any(role.name in LEADER_ROLES for role in interaction.user.roles):
            await interaction.response.send_message(f"🚫 ไม่มีสิทธิ์ถอนเงิน! (ต้องมี Role: {', '.join(LEADER_ROLES)})", ephemeral=True); return

        await interaction.response.defer(ephemeral=True, thinking=True)
        success = await update_bank_action(amount, self.action_type, interaction.user, reason)
        await send_bank_log(self.original_channel, amount, self.action_type, success, reason, interaction.user)
        await interaction.followup.send("ทำรายการสำเร็จ!" if success else "ทำรายการไม่สำเร็จ (เงินอาจไม่พอ)", ephemeral=True)
        await setup_inventory_control_panel()

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        print(f"{log_ts()} Error in BankTransactionModal: {error}"); traceback.print_exc()
        try:
            if not interaction.response.is_done(): await interaction.response.send_message("เกิดข้อผิดพลาดในการดำเนินการ Modal", ephemeral=True)
            else: await interaction.followup.send("เกิดข้อผิดพลาดในการดำเนินการ Modal", ephemeral=True)
        except Exception as e_resp: print(f"{log_ts()} Error sending error response in BankModal: {e_resp}")

class PersistentInventoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persistent view

    async def _handle_item_action(self, interaction: discord.Interaction, action_type: str):
        # Check permissions first for withdraw
        if action_type == "withdraw" and not any(role.name in LEADER_ROLES for role in interaction.user.roles):
            await interaction.response.send_message(f"🚫 ไม่มีสิทธิ์เบิกของ! (ต้องมี Role: {', '.join(LEADER_ROLES)})", ephemeral=True)
            return

        items_for_selection = []
        if action_type == "deposit":
            items_for_selection = AVAILABLE_ITEMS # User can deposit any defined item
        elif action_type == "withdraw":
            items_for_selection = [item for item in AVAILABLE_ITEMS if team_inventory.get(item, 0) > 0] # Only items in stock

        if not items_for_selection:
            message = "⚠️ ไม่มีไอเทมให้เบิกในคลัง!" if action_type == "withdraw" else "⚠️ ไม่มีรายการไอเทมที่กำหนดไว้ในระบบ!"
            await interaction.response.send_message(message, ephemeral=True)
            return

        view = EphemeralItemSelectView(action_type, items_for_selection, interaction.user.id, interaction.channel)
        # Send the ephemeral message and store it on the view if possible for timeout handling.
        # For ephemeral messages, interaction.original_response() might be needed later.
        await interaction.response.send_message("เลือกไอเทม:", view=view, ephemeral=True)
        try:
            # Get the message object for the ephemeral response to potentially edit it on timeout
            view.message = await interaction.original_response()
        except discord.HTTPException:
            print(f"{log_ts()} Could not get original response for ephemeral item select view. Timeout edit might not work.")


    @discord.ui.button(label="📥 ฝากของ", style=discord.ButtonStyle.green, custom_id="persistent_deposit_item_v2")
    async def deposit_item_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_item_action(interaction, "deposit")

    @discord.ui.button(label="📤 เบิกของ", style=discord.ButtonStyle.red, custom_id="persistent_withdraw_item_v2")
    async def withdraw_item_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_item_action(interaction, "withdraw")

    @discord.ui.button(label="💰 ฝากเงิน", style=discord.ButtonStyle.success, custom_id="persistent_deposit_money_v2")
    async def deposit_money_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # No role check needed for deposit
        await interaction.response.send_modal(BankTransactionModal("deposit", "ฝากเงินเข้าคลัง", interaction.channel))

    @discord.ui.button(label="💸 ถอนเงิน", style=discord.ButtonStyle.danger, custom_id="persistent_withdraw_money_v2")
    async def withdraw_money_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(role.name in LEADER_ROLES for role in interaction.user.roles):
            await interaction.response.send_message(f"🚫 ไม่มีสิทธิ์ถอนเงิน! (ต้องมี Role: {', '.join(LEADER_ROLES)})", ephemeral=True)
            return
        await interaction.response.send_modal(BankTransactionModal("withdraw", "ถอนเงินจากคลัง", interaction.channel))


# --- Embed Creation ---
def create_control_panel_embed():
    load_data() # Load latest data before creating embed
    embed = discord.Embed(title="📦 ระบบคลังกลางทีม (v2) 📦", description="คลิกปุ่มด้านล่างเพื่อดำเนินการ", color=discord.Color.blue()) # Changed color
    # Displaying items: show all items with their quantities, even if 0, or only > 0?
    # For this example, show all defined items.
    summary_lines = []
    for item_name in AVAILABLE_ITEMS: # Iterate in defined order
        qty = team_inventory.get(item_name, 0)
        summary_lines.append(f"• {item_name}: **{qty}** ชิ้น")

    # Paginate if too long, or just show a subset
    max_items_in_embed = 7 # Adjust as needed
    if len(summary_lines) > max_items_in_embed:
        summary_text = "\n".join(summary_lines[:max_items_in_embed])
        summary_text += f"\n*และอีก {len(summary_lines) - max_items_in_embed} รายการ... ใช้คำสั่ง {bot.command_prefix}ดูของ เพื่อดูทั้งหมด*"
    elif summary_lines:
        summary_text = "\n".join(summary_lines)
    else:
        summary_text = "ยังไม่มีของในคลัง / ไม่มีไอเทมที่กำหนด"

    embed.add_field(name="ยอดของในคลัง", value=summary_text, inline=False)
    embed.add_field(name="ยอดเงินคงเหลือ", value=f"**{team_bank.get('balance', 0):,}** บาท", inline=False) # Use .get for bank balance
    embed.set_footer(text=f"อัปเดตล่าสุด: {datetime.now(TZ_BANGKOK).strftime('%d/%m/%Y %H:%M:%S')}")
    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    return embed

# --- Control Panel Setup ---
def get_control_panel_message_id():
    try:
        with open(CONTROL_PANEL_MESSAGE_ID_FILE, 'r') as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, TypeError):
        return None

def save_control_panel_message_id(message_id: int):
    try:
        with open(CONTROL_PANEL_MESSAGE_ID_FILE, 'w') as f:
            f.write(str(message_id))
    except Exception as e:
        print(f"{log_ts()} Error saving Control Panel message ID: {e}")

async def delete_old_control_panel(channel: discord.TextChannel):
    old_message_id = get_control_panel_message_id()
    if old_message_id:
        try:
            message = await channel.fetch_message(old_message_id)
            await message.delete()
            print(f"{log_ts()} Deleted old control panel (ID: {old_message_id})")
        except discord.NotFound:
            print(f"{log_ts()} Old control panel (ID: {old_message_id}) not found. Already deleted?")
        except discord.Forbidden:
            print(f"{log_ts()} ERROR: No permission to delete old panel (ID: {old_message_id}) in {channel.name}")
        except Exception as e:
            print(f"{log_ts()} Error deleting old control panel (ID: {old_message_id}): {e}")
        finally:
            # Remove the ID file regardless of deletion success if it existed
            if os.path.exists(CONTROL_PANEL_MESSAGE_ID_FILE):
                try: os.remove(CONTROL_PANEL_MESSAGE_ID_FILE)
                except OSError as e_rm: print(f"{log_ts()} Error removing {CONTROL_PANEL_MESSAGE_ID_FILE}: {e_rm}")


async def setup_inventory_control_panel(force_new: bool = False):
    print(f"{log_ts()} Attempting to setup/update inventory control panel (force_new={force_new})")
    if not CONTROL_PANEL_CHANNEL_ID: # Check if ID is set
        print(f"{log_ts()} !!! CRITICAL: CONTROL_PANEL_CHANNEL_ID is not set. Skipping panel setup.")
        return

    channel = bot.get_channel(CONTROL_PANEL_CHANNEL_ID)
    if not channel:
        print(f"{log_ts()} !!! CRITICAL: Control panel channel (ID: {CONTROL_PANEL_CHANNEL_ID}) not found. Bot may not have access or ID is incorrect.")
        return
    if not isinstance(channel, discord.TextChannel):
        print(f"{log_ts()} !!! CRITICAL: Control panel channel (ID: {CONTROL_PANEL_CHANNEL_ID}) is not a TextChannel.")
        return

    current_embed = create_control_panel_embed()
    persistent_view = PersistentInventoryView() # Always create a new view instance for sending/editing

    message_id_to_edit = get_control_panel_message_id()
    message_object_to_edit = None

    if force_new:
        print(f"{log_ts()} Force_new is True. Deleting old panel if exists.")
        await delete_old_control_panel(channel)
        message_id_to_edit = None # Ensure we create a new one

    if not force_new and message_id_to_edit:
        try:
            message_object_to_edit = await channel.fetch_message(message_id_to_edit)
            print(f"{log_ts()} Found existing panel (ID: {message_id_to_edit}) to edit.")
        except discord.NotFound:
            print(f"{log_ts()} Panel message (ID: {message_id_to_edit}) not found. Will create a new one.")
            if os.path.exists(CONTROL_PANEL_MESSAGE_ID_FILE): os.remove(CONTROL_PANEL_MESSAGE_ID_FILE) # Clean up stale ID file
            message_id_to_edit = None # Clear to ensure new message creation
        except discord.Forbidden:
            print(f"{log_ts()} ERROR: No permission to fetch panel message (ID: {message_id_to_edit}). Will try to create new.")
            message_id_to_edit = None
        except Exception as e:
            print(f"{log_ts()} Error fetching panel message (ID: {message_id_to_edit}): {e}. Will try to create new.")
            message_id_to_edit = None


    try:
        if message_object_to_edit and not force_new : # Edit existing if found and not forced new
            await message_object_to_edit.edit(embed=current_embed, view=persistent_view)
            print(f"{log_ts()} Successfully UPDATED control panel (ID: {message_object_to_edit.id}).")
        else: # Create new panel
            new_message = await channel.send(embed=current_embed, view=persistent_view)
            save_control_panel_message_id(new_message.id)
            print(f"{log_ts()} Successfully CREATED NEW control panel (ID: {new_message.id}).")
    except discord.Forbidden:
        print(f"{log_ts()} !!! CRITICAL ERROR: Bot lacks permissions (Send Messages or Embed Links or Use External Emojis or Add Reactions) in channel ID {CONTROL_PANEL_CHANNEL_ID} to setup panel.")
    except Exception as e:
        print(f"{log_ts()} !!! CRITICAL ERROR during final panel setup (send/edit): {e}")
        traceback.print_exc()


# --- Scheduled Task ---
# Correctly calculate next run time for tasks.loop
def get_next_refresh_time_utc():
    # Desired refresh time in Bangkok
    target_hour_bkk_refresh = 8 # e.g., 8 AM Bangkok time
    target_minute_bkk_refresh = 0

    now_utc = datetime.now(pytz.utc)
    now_bkk_for_calc = now_utc.astimezone(TZ_BANGKOK)

    next_run_bkk = now_bkk_for_calc.replace(hour=target_hour_bkk_refresh, minute=target_minute_bkk_refresh, second=0, microsecond=0)

    if next_run_bkk <= now_bkk_for_calc: # If target time for today has passed
        next_run_bkk += timedelta(days=1)

    next_run_utc = next_run_bkk.astimezone(pytz.utc)
    print(f"{log_ts()} Next daily_panel_refresh scheduled for: {next_run_bkk.strftime('%Y-%m-%d %H:%M:%S %Z')} (BKK) / {next_run_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} (UTC)")
    return next_run_utc.timetz() # tasks.loop expects a datetime.time object in UTC

# Initialize REFRESH_TIME_UTC for the first run.
# tasks.loop will use this and then internally calculate the next run based on a 24-hour interval from this time.
REFRESH_TIME_UTC = get_next_refresh_time_utc()
print(f"{log_ts()} Initial REFRESH_TIME_UTC for tasks.loop: {REFRESH_TIME_UTC.strftime('%H:%M:%S %Z')}")


@tasks.loop(time=REFRESH_TIME_UTC) # This time is UTC
async def daily_panel_refresh():
    print(f"{log_ts()} === Daily Panel Refresh Task Triggered ===")
    print(f"{log_ts()} Expected UTC for this run: {REFRESH_TIME_UTC.strftime('%H:%M:%S %Z')}")
    print(f"{log_ts()} Actual current UTC: {datetime.now(pytz.utc).strftime('%H:%M:%S %Z')}")
    try:
        await setup_inventory_control_panel(force_new=True)
        print(f"{log_ts()} Daily panel refresh task execution completed successfully.")
    except Exception as e_task:
        print(f"{log_ts()} !!! ERROR during daily_panel_refresh execution: {e_task}")
        traceback.print_exc()
    print(f"{log_ts()} === Daily Panel Refresh Task Finished ===")
    # The loop will automatically schedule for the same UTC time next day.


# --- Bot Commands ---
@bot.command(name="ดูของ", aliases=["คลัง", "inventory"])
async def show_inventory_command(ctx):
    load_data() # Ensure latest data
    embed = discord.Embed(title="📦 สรุปยอดคลังกลางทั้งหมด 📦", color=discord.Color.gold())
    item_list_str = "\n".join([f"• {name}: **{team_inventory.get(name, 0)}** ชิ้น" for name in AVAILABLE_ITEMS]) # Show all items
    if not any(team_inventory.get(name, 0) > 0 for name in AVAILABLE_ITEMS):
        item_list_str = "ยังไม่มีของในคลัง"

    embed.add_field(name="รายการของในคลัง", value=item_list_str, inline=False)
    embed.add_field(name="ยอดเงินคงเหลือ", value=f"**{team_bank.get('balance', 0):,}** บาท", inline=False)
    embed.set_footer(text=f"ข้อมูล ณ {datetime.now(TZ_BANGKOK).strftime('%d/%m/%Y %H:%M:%S')}")
    await ctx.send(embed=embed)

@bot.command(name="บังคับรีเฟรชพาเนล", aliases=["forcepanel", "refreshpanel", "updatepanel"])
@commands.has_permissions(administrator=True) # Or specific roles
async def force_refresh_panel_command(ctx):
    msg_feedback = await ctx.send("🔄 กำลังบังคับรีเฟรช Control Panel...", delete_after=15)
    try:
        await setup_inventory_control_panel(force_new=True)
        await msg_feedback.edit(content="✅ Control Panel ถูกรีเฟรชเรียบร้อยแล้ว!", delete_after=10)
    except Exception as e:
        await msg_feedback.edit(content=f"❌ เกิดข้อผิดพลาดในการรีเฟรช: {e}", delete_after=15)
        print(f"{log_ts()} Error in force_refresh_panel_command: {e}")
        traceback.print_exc()

@force_refresh_panel_command.error
async def force_refresh_panel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 คุณไม่มีสิทธิ์ใช้คำสั่งนี้", ephemeral=True, delete_after=10)
    else:
        await ctx.send(f"เกิดข้อผิดพลาด: {error}", ephemeral=True, delete_after=10)
        print(f"{log_ts()} Error in force_refresh_panel_command (handler): {error}")

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f"{log_ts()} Bot {bot.user.name} ({bot.user.id}) is attempting to connect and initialize...")
    load_data()
    print(f"{log_ts()} Initial data loaded.")

    # Register persistent view if not already done (important for restarts)
    # Check if a view with the same custom_ids is already registered; discord.py handles this better in recent versions.
    # A simpler check is just to add it. If it's already there, discord.py might ignore or log.
    # For robustness, you might want to track if you've added it in this session.
    if not bot.persistent_views: # Or a more specific check if you have multiple persistent views
        bot.add_view(PersistentInventoryView())
        print(f"{log_ts()} PersistentInventoryView registered with the bot.")
    else:
        # Check if our specific view is among them
        # This check is a bit tricky as you'd need to compare types or custom_ids.
        # For simplicity, if any persistent view exists, we assume ours might be among them from a previous (failed) on_ready.
        # Re-adding is generally safe.
        print(f"{log_ts()} Bot already has persistent views. Attempting to add/re-add ours.")
        bot.add_view(PersistentInventoryView()) # Re-adding is generally okay.

    await setup_inventory_control_panel(force_new=False) # Attempt to update or create panel
    print(f"{log_ts()} Initial Control Panel setup/update completed.")

    try:
        await bot.change_presence(activity=discord.Game(name=f"ดูแลคลัง | {bot.command_prefix}คลัง"))
        print(f"{log_ts()} Bot presence set.")
    except Exception as e_presence:
        print(f"{log_ts()} Error setting bot presence: {e_presence}")


    print(f"{log_ts()} Attempting to start daily_panel_refresh task loop...")
    if not daily_panel_refresh.is_running():
        try:
            daily_panel_refresh.start()
            if daily_panel_refresh.is_running():
                print(f"{log_ts()} Daily panel refresh task STARTED. Next run based on calculated UTC time.")
            else:
                print(f"!!! {log_ts()} CRITICAL WARNING: daily_panel_refresh.start() was called, but task IS NOT RUNNING. Check task logic and conditions.")
        except RuntimeError as e_runtime: # e.g. "Event loop is closed"
             print(f"!!! {log_ts()} CRITICAL RuntimeError starting daily_panel_refresh task (event loop issue?): {e_runtime}")
             traceback.print_exc()
        except Exception as e_task_start:
            print(f"!!! {log_ts()} CRITICAL EXCEPTION starting daily_panel_refresh task: {e_task_start}")
            traceback.print_exc()
    else:
        print(f"{log_ts()} Daily panel refresh task is ALREADY running.")

    print(f"{log_ts()} ------ Bot {bot.user.name} is fully ready and online! ------")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return # Silently ignore unknown commands
    elif isinstance(error, (commands.CheckFailure, commands.MissingPermissions, commands.MissingAnyRole, commands.NoPrivateMessage)):
        try:
            await ctx.send("🚫 คุณไม่มีสิทธิ์ใช้คำสั่งนี้ หรือไม่สามารถใช้ใน DM ได้", ephemeral=True, delete_after=10)
        except discord.HTTPException:
            pass # Ignore if cannot send response (e.g., channel deleted)
    elif isinstance(error, commands.CommandInvokeError):
        original = error.original
        print(f'{log_ts()} Error in command {ctx.command}: {original}')
        traceback.print_tb(original.__traceback__)
        try:
            await ctx.send(f"เกิดข้อผิดพลาดขณะรันคำสั่ง: `{original.__class__.__name__}`. แจ้งผู้ดูแล.", ephemeral=True, delete_after=15)
        except discord.HTTPException:
            pass
    else:
        print(f'{log_ts()} Unhandled command error for command "{ctx.command}" by "{ctx.author}": {error}')
        traceback.print_exc()

# --- START: Keep Alive Web Server ---
flask_app = Flask('')

@flask_app.route('/')
def flask_home():
    return "Inventory Bot (TeamFight) is alive and well!" # Personalized message

def run_flask():
  # Get port from environment variable or default to 8080 for local
  port = int(os.environ.get('PORT', 8080))
  print(f"{log_ts()} Flask server attempting to run on host 0.0.0.0, port {port}")
  try:
    flask_app.run(host='0.0.0.0', port=port)
    print(f"{log_ts()} Flask server started successfully on port {port}.") # This line might not be reached if run blocks
  except Exception as e_flask_run:
    print(f"{log_ts()} !!! ERROR starting Flask server: {e_flask_run}")
    traceback.print_exc()


def start_flask_server_if_needed():
    # Run Flask server if not on Replit (Replit has its own keep-alive)
    # On Render, we need this to be a web service.
    # You could add another env var to explicitly disable Flask if needed for other environments.
    # For Render deployment, we WANT this to run.
    print(f"{log_ts()} Starting Flask server for web service...")
    t = Thread(target=run_flask)
    t.daemon = True # Ensures thread exits when main program exits
    t.start()
    print(f"{log_ts()} Flask server thread initiated.")

# --- END: Keep Alive Web Server ---


# --- Run Bot ---
if __name__ == '__main__':
    BOT_TOKEN = os.environ.get('INVENTORY_BOT_TOKEN') # ใช้ชื่อนี้บน Render Env Vars

    if BOT_TOKEN:
        try:
            start_flask_server_if_needed() # Start Flask server in a thread
            print(f"{log_ts()} Attempting to run Discord bot with token: ...{BOT_TOKEN[-6:]}")
            bot.run(BOT_TOKEN)
        except discord.errors.LoginFailure:
            print(f"{log_ts()} !!! CRITICAL LOGIN FAILURE: Improper token. Check your INVENTORY_BOT_TOKEN environment variable. !!!")
        except discord.errors.PrivilegedIntentsRequired:
            print(f"{log_ts()} !!! CRITICAL INTENTS ERROR: Privileged server members and/or message content intent not enabled on Discord Developer Portal. !!!")
        except Exception as e_main_run:
            print(f"{log_ts()} !!! AN UNEXPECTED CRITICAL ERROR occurred during bot.run(): {e_main_run} !!!")
            traceback.print_exc()
    else:
        print(f"{log_ts()} !!! BOT TOKEN NOT FOUND: 'INVENTORY_BOT_TOKEN' environment variable is missing. Bot cannot start. !!!")
        print(f"{log_ts()} Please set the INVENTORY_BOT_TOKEN environment variable (e.g., in a .env file for local, or in Render's settings).")