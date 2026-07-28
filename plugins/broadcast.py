from pyrogram import Client, filters
from pyrogram.types import Message

from tools import redis_client, scan_keys, is_admin

@Client.on_message(filters.command("broadcast") & filters.private)
async def broadcast_command(client: Client, message: Message):
    user_id = message.from_user.id
    
    # Check if user is admin
    if not is_admin(user_id):
        await message.reply_text("❌ You don't have admin privileges.")
        return
    
    # Check if this is a reply to a message
    if not message.reply_to_message:
        await message.reply_text(
            "📢 **Broadcast Command**\n\n"
            "Please reply to a message you want to broadcast.\n\n"
            "**Usage:**\n"
            "• `/broadcast` - Broadcast with author info\n"
            "• `/broadcast -f` - Broadcast without author info\n\n"
            "**Example:**\n"
            "Reply to any message with `/broadcast` to send it to all users."
        )
        return
    
    try:
        # Check for -f flag to drop author
        command_parts = message.text.split()
        drop_author = "-f" in command_parts
        
        # Get all user IDs from Redis
        user_keys = scan_keys("user_token:*")
        stored_user_ids = [int(key.split(":")[1]) for key in user_keys]
        total_users = len(stored_user_ids)
        
        if total_users == 0:
            await message.reply_text("❌ No users found in database.")
            return
        
        # Send initial status message using native send_message_draft
        draft_id = client.rnd_id()
        await client.send_message_draft(
            chat_id=message.chat.id,
            draft_id=draft_id,
            text=(
                f"📢 **Broadcast Started**\n\n"
                f"👥 Sending to {total_users} users...\n"
                f"📝 Drop author: {'Yes' if drop_author else 'No'}"
            )
        )
        
        reply_message = message.reply_to_message
        sent_count = 0
        failed_count = 0
        removed_users = 0
        
        for user_id in stored_user_ids:
            try:
                if drop_author:
                    # Send as copy without forwarding (drops author info)
                    if reply_message.text:
                        await client.send_message(user_id, reply_message.text)
                    elif reply_message.photo:
                        await client.send_photo(
                            user_id, 
                            reply_message.photo.file_id,
                            caption=reply_message.caption or ""
                        )
                    elif reply_message.video:
                        await client.send_video(
                            user_id,
                            reply_message.video.file_id,
                            caption=reply_message.caption or ""
                        )
                    elif reply_message.document:
                        await client.send_document(
                            user_id,
                            reply_message.document.file_id,
                            caption=reply_message.caption or ""
                        )
                    else:
                        # For other message types, forward normally
                        await client.forward_messages(user_id, message.chat.id, reply_message.id)
                else:
                    # Forward with author info
                    await client.forward_messages(user_id, message.chat.id, reply_message.id)
                
                sent_count += 1
                
                # Update status progress using native send_message_draft
                if sent_count % 10 == 0 or sent_count == total_users:
                    await client.send_message_draft(
                        chat_id=message.chat.id,
                        draft_id=draft_id,
                        text=(
                            f"📢 **Broadcast Progress**\n\n"
                            f"✅ Sent: {sent_count}/{total_users}\n"
                            f"❌ Failed: {failed_count}\n"
                            f"🗑️ Removed: {removed_users}\n\n"
                            f"⏳ Processing..."
                        )
                    )
                
            except Exception as e:
                error_str = str(e).lower()
                
                # Check if user is unreachable/blocked the bot
                if any(keyword in error_str for keyword in ["user not found", "blocked", "forbidden", "chat not found"]):
                    # Remove user from Redis if they're unreachable
                    try:
                        redis_client.delete(f"user_token:{user_id}")
                        redis_client.delete(f"user_requests:{user_id}")
                        removed_users += 1
                    except Exception:
                        pass
                
                failed_count += 1
        
        # Send final status using standard send_message
        final_message = (
            f"📢 **Broadcast Completed**\n\n"
            f"✅ Successfully sent: **{sent_count}**\n"
            f"❌ Failed: **{failed_count}**\n"
            f"🗑️ Removed invalid users: **{removed_users}**\n\n"
            f"📊 **Summary:**\n"
            f"• Total attempted: {total_users}\n"
            f"• Success rate: {round((sent_count/total_users)*100, 1)}%\n"
            f"• Author info: {'Dropped' if drop_author else 'Preserved'}"
        )
        
        await client.send_message(message.chat.id, final_message)
        
    except Exception as e:
        await message.reply_text(f"❌ Broadcast failed: {str(e)}")

