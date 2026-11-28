package kt.sanatani.wapower.db

import androidx.room.ColumnInfo
import androidx.room.Embedded
import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "chats",
    indices = [
        Index(value = ["archived", "pinned", "last_ts"], name = "idx_chats_sort"),
    ],
)
data class ChatEntity(
    @PrimaryKey
    @ColumnInfo(name = "jid")
    val jid: String,
    @ColumnInfo(name = "name")
    val name: String = "",
    @ColumnInfo(name = "is_group")
    val isGroup: Boolean = false,
    @ColumnInfo(name = "last_msg_id")
    val lastMsgId: String = "",
    @ColumnInfo(name = "last_preview")
    val lastPreview: String = "",
    @ColumnInfo(name = "last_ts")
    val lastTs: Long = 0L,
    @ColumnInfo(name = "unread_count")
    val unreadCount: Int = 0,
    @ColumnInfo(name = "muted_until")
    val mutedUntil: Long = 0L,
    @ColumnInfo(name = "pinned")
    val pinned: Boolean = false,
    @ColumnInfo(name = "archived")
    val archived: Boolean = false,
    @ColumnInfo(name = "avatar_path")
    val avatarPath: String = "",
    @ColumnInfo(name = "updated_at")
    val updatedAt: Long = 0L,
)

@Entity(
    tableName = "messages",
    indices = [
        Index(value = ["chat_id", "ts", "id"], name = "idx_msg_chat_ts"),
    ],
)
data class MessageEntity(
    @PrimaryKey
    @ColumnInfo(name = "id")
    val id: String,
    @ColumnInfo(name = "chat_id")
    val chatId: String,
    @ColumnInfo(name = "sender_id")
    val senderId: String = "",
    @ColumnInfo(name = "from_me")
    val fromMe: Boolean = false,
    @ColumnInfo(name = "ts")
    val ts: Long = 0L,
    @ColumnInfo(name = "msg_type")
    val msgType: String = "text",
    @ColumnInfo(name = "body")
    val body: String = "",
    @ColumnInfo(name = "media_path")
    val mediaPath: String = "",
    @ColumnInfo(name = "media_mime")
    val mediaMime: String = "",
    @ColumnInfo(name = "quoted_id")
    val quotedId: String = "",
    @ColumnInfo(name = "reactions_json")
    val reactionsJson: String = "{}",
    @ColumnInfo(name = "receipt_status")
    val receiptStatus: String = "sent",
    @ColumnInfo(name = "is_deleted")
    val isDeleted: Boolean = false,
    @ColumnInfo(name = "original_body")
    val originalBody: String = "",
    @ColumnInfo(name = "is_edited")
    val isEdited: Boolean = false,
    @ColumnInfo(name = "is_view_once")
    val isViewOnce: Boolean = false,
    @ColumnInfo(name = "raw_json")
    val rawJson: String = "",
)

@Entity(
    tableName = "contacts",
    indices = [
        Index(value = ["full_name", "push_name", "phone"], name = "idx_contacts_name"),
    ],
)
data class ContactEntity(
    @PrimaryKey
    @ColumnInfo(name = "jid")
    val jid: String,
    @ColumnInfo(name = "phone")
    val phone: String = "",
    @ColumnInfo(name = "push_name")
    val pushName: String = "",
    @ColumnInfo(name = "full_name")
    val fullName: String = "",
    @ColumnInfo(name = "business_name")
    val businessName: String = "",
    @ColumnInfo(name = "about")
    val about: String = "",
    @ColumnInfo(name = "avatar_path")
    val avatarPath: String = "",
    @ColumnInfo(name = "is_online")
    val isOnline: Boolean = false,
    @ColumnInfo(name = "is_typing")
    val isTyping: Boolean = false,
    @ColumnInfo(name = "last_seen_ts")
    val lastSeenTs: Long = 0L,
    @ColumnInfo(name = "updated_at")
    val updatedAt: Long = 0L,
)

@Entity(
    tableName = "call_log",
    indices = [
        Index(value = ["started_at"], name = "idx_call_started_at"),
    ],
)
data class CallLogEntity(
    @PrimaryKey
    @ColumnInfo(name = "call_id")
    val callId: String,
    @ColumnInfo(name = "peer_jid")
    val peerJid: String,
    @ColumnInfo(name = "direction")
    val direction: String = "in",
    @ColumnInfo(name = "is_video")
    val isVideo: Boolean = false,
    @ColumnInfo(name = "status")
    val status: String = "ringing",
    @ColumnInfo(name = "started_at")
    val startedAt: Long = 0L,
    @ColumnInfo(name = "ended_at")
    val endedAt: Long = 0L,
    @ColumnInfo(name = "duration_sec")
    val durationSec: Long = 0L,
)

data class ChatListRow(
    @Embedded val chat: ChatEntity,
    @ColumnInfo(name = "contact_full_name") val contactFullName: String? = null,
    @ColumnInfo(name = "contact_push_name") val contactPushName: String? = null,
    @ColumnInfo(name = "contact_avatar") val contactAvatar: String? = null,
    @ColumnInfo(name = "is_online") val isOnline: Boolean = false,
    @ColumnInfo(name = "is_typing") val isTyping: Boolean = false,
    @ColumnInfo(name = "last_seen_ts") val lastSeenTs: Long = 0L,
) {
    val displayName: String
        get() = chat.name.ifBlank {
            contactFullName?.takeIf { it.isNotBlank() }
                ?: contactPushName?.takeIf { it.isNotBlank() }
                ?: chat.jid
        }

    val resolvedAvatar: String
        get() = chat.avatarPath.ifBlank { contactAvatar.orEmpty() }
}

data class UnreadMessageRef(
    @ColumnInfo(name = "id") val id: String,
    @ColumnInfo(name = "sender_id") val senderId: String,
)
