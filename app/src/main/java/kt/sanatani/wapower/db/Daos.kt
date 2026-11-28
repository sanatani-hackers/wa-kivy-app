package kt.sanatani.wapower.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow
import org.json.JSONObject

object ReceiptRank {
    fun rankOf(status: String): Int = when (status) {
        "", "pending", "failed" -> 0
        "sent" -> 1
        "delivered" -> 2
        "read" -> 3
        "played" -> 4
        else -> 0
    }
}

fun normalizeJid(jid: String?): String {
    val s = jid.orEmpty().trim()
    val at = s.indexOf('@')
    if (at < 0) return s
    val userPart = s.substring(0, at)
    val server = s.substring(at + 1)
    val cleanUser = userPart.substringBefore(':')
    return "$cleanUser@$server"
}

@Dao
interface ChatDao {
    @Query(
        """
        SELECT ch.*,
               ct.full_name AS contact_full_name,
               ct.push_name AS contact_push_name,
               ct.avatar_path AS contact_avatar,
               COALESCE(ct.is_online, 0) AS is_online,
               COALESCE(ct.is_typing, 0) AS is_typing,
               COALESCE(ct.last_seen_ts, 0) AS last_seen_ts
        FROM chats ch
        LEFT JOIN contacts ct ON ct.jid = ch.jid
        WHERE ch.archived = 0
        ORDER BY ch.pinned DESC, ch.last_ts DESC, ch.updated_at DESC
        """
    )
    fun observeActiveChats(): Flow<List<ChatListRow>>

    @Query(
        """
        SELECT ch.*,
               ct.full_name AS contact_full_name,
               ct.push_name AS contact_push_name,
               ct.avatar_path AS contact_avatar,
               COALESCE(ct.is_online, 0) AS is_online,
               COALESCE(ct.is_typing, 0) AS is_typing,
               COALESCE(ct.last_seen_ts, 0) AS last_seen_ts
        FROM chats ch
        LEFT JOIN contacts ct ON ct.jid = ch.jid
        WHERE ch.archived = 1
        ORDER BY ch.pinned DESC, ch.last_ts DESC, ch.updated_at DESC
        """
    )
    fun observeArchivedChats(): Flow<List<ChatListRow>>

    @Query("SELECT COUNT(*) FROM chats")
    fun observeCount(): Flow<Int>

    @Query("SELECT * FROM chats WHERE jid = :jid LIMIT 1")
    suspend fun getByJid(jid: String): ChatEntity?

    @Query(
        """
        SELECT ch.*,
               ct.full_name AS contact_full_name,
               ct.push_name AS contact_push_name,
               ct.avatar_path AS contact_avatar,
               COALESCE(ct.is_online, 0) AS is_online,
               COALESCE(ct.is_typing, 0) AS is_typing,
               COALESCE(ct.last_seen_ts, 0) AS last_seen_ts
        FROM chats ch
        LEFT JOIN contacts ct ON ct.jid = ch.jid
        WHERE ch.jid = :jid
        LIMIT 1
        """
    )
    fun observeChat(jid: String): Flow<ChatListRow?>

    @Query("UPDATE chats SET pinned = :pinned, updated_at = :now WHERE jid = :jid")
    suspend fun setPinned(jid: String, pinned: Boolean, now: Long = System.currentTimeMillis() / 1000L)

    @Query("UPDATE chats SET archived = :archived, updated_at = :now WHERE jid = :jid")
    suspend fun setArchived(jid: String, archived: Boolean, now: Long = System.currentTimeMillis() / 1000L)

    @Query("UPDATE chats SET muted_until = :mutedUntil, updated_at = :now WHERE jid = :jid")
    suspend fun setMutedUntil(jid: String, mutedUntil: Long, now: Long = System.currentTimeMillis() / 1000L)

    @Query("UPDATE chats SET avatar_path = :avatarPath, updated_at = :now WHERE jid = :jid")
    suspend fun updateAvatarPath(jid: String, avatarPath: String, now: Long = System.currentTimeMillis() / 1000L)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertOrReplace(chat: ChatEntity)

    @Query("UPDATE chats SET unread_count = 0, updated_at = :now WHERE jid = :jid")
    suspend fun clearUnread(jid: String, now: Long = System.currentTimeMillis() / 1000L)

    @Transaction
    suspend fun upsertChat(
        jidRaw: String,
        name: String? = null,
        isGroup: Boolean? = null,
        lastMsgId: String? = null,
        lastPreview: String? = null,
        lastTs: Long? = null,
        unreadCount: Int? = null,
        unreadDelta: Int = 0,
        mutedUntil: Long? = null,
        pinned: Boolean? = null,
        archived: Boolean? = null,
        avatarPath: String? = null,
    ) {
        val jid = normalizeJid(jidRaw)
        if (jid.isBlank()) return
        val now = System.currentTimeMillis() / 1000L
        val existing = getByJid(jid)
        if (existing == null) {
            insertOrReplace(
                ChatEntity(
                    jid = jid,
                    name = name?.trim().orEmpty(),
                    isGroup = isGroup ?: jid.endsWith("@g.us"),
                    lastMsgId = lastMsgId.orEmpty(),
                    lastPreview = lastPreview.orEmpty(),
                    lastTs = lastTs ?: 0L,
                    unreadCount = unreadCount ?: maxOf(0, unreadDelta),
                    mutedUntil = mutedUntil ?: 0L,
                    pinned = pinned ?: false,
                    archived = archived ?: false,
                    avatarPath = avatarPath.orEmpty(),
                    updatedAt = now,
                )
            )
            return
        }

        val newUnread = when {
            unreadCount != null -> maxOf(0, unreadCount)
            unreadDelta != 0 -> maxOf(0, existing.unreadCount + unreadDelta)
            else -> existing.unreadCount
        }
        var newTs = existing.lastTs
        var newMid = existing.lastMsgId
        var newPrev = existing.lastPreview
        if (lastTs != null && lastTs >= newTs) {
            newTs = lastTs
            if (lastMsgId != null) newMid = lastMsgId
            if (lastPreview != null) newPrev = lastPreview
        } else if (lastPreview != null && newPrev.isBlank()) {
            newPrev = lastPreview
        }

        insertOrReplace(
            existing.copy(
                name = name?.takeIf { it.isNotBlank() }?.trim() ?: existing.name,
                isGroup = isGroup ?: existing.isGroup,
                lastMsgId = newMid,
                lastPreview = newPrev,
                lastTs = newTs,
                unreadCount = newUnread,
                mutedUntil = mutedUntil ?: existing.mutedUntil,
                pinned = pinned ?: existing.pinned,
                archived = archived ?: existing.archived,
                avatarPath = avatarPath?.takeIf { it.isNotBlank() } ?: existing.avatarPath,
                updatedAt = now,
            )
        )
    }
}

@Dao
interface MessageDao {
    @Query("SELECT * FROM messages WHERE chat_id = :chatId ORDER BY ts ASC, id ASC")
    fun observeMessages(chatId: String): Flow<List<MessageEntity>>

    @Query("SELECT COUNT(*) FROM messages")
    fun observeCount(): Flow<Int>

    @Query("SELECT * FROM messages WHERE id = :id LIMIT 1")
    suspend fun getById(id: String): MessageEntity?

    @Query("SELECT id, sender_id FROM messages WHERE chat_id = :chatId AND from_me = 0 AND receipt_status != 'read'")
    suspend fun getUnreadIncomingRefs(chatId: String): List<UnreadMessageRef>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertOrReplace(msg: MessageEntity)

    @Query("UPDATE messages SET receipt_status = 'read' WHERE chat_id = :chatId AND from_me = 0")
    suspend fun markIncomingReadLocal(chatId: String)

    @Query("UPDATE messages SET media_path = :mediaPath, media_mime = CASE WHEN :mediaMime != '' THEN :mediaMime ELSE media_mime END WHERE id = :msgId")
    suspend fun updateMediaPath(msgId: String, mediaPath: String, mediaMime: String = "")

    @Query("DELETE FROM messages WHERE id = :id")
    suspend fun deleteById(id: String)

    @Transaction
    suspend fun upsertMessage(incoming: MessageEntity) {
        val normChat = normalizeJid(incoming.chatId)
        val normSender = normalizeJid(incoming.senderId)
        if (incoming.id.isBlank() || normChat.isBlank()) return

        val existing = getById(incoming.id)
        if (existing == null) {
            insertOrReplace(incoming.copy(chatId = normChat, senderId = normSender))
            return
        }

        val finalReceipt = if (ReceiptRank.rankOf(incoming.receiptStatus) >= ReceiptRank.rankOf(existing.receiptStatus)) {
            incoming.receiptStatus
        } else {
            existing.receiptStatus
        }

        insertOrReplace(
            existing.copy(
                senderId = normSender.ifBlank { existing.senderId },
                fromMe = incoming.fromMe,
                ts = if (incoming.ts > 0) incoming.ts else existing.ts,
                msgType = incoming.msgType.ifBlank { existing.msgType },
                body = if (existing.isDeleted) existing.body else incoming.body.ifBlank { existing.body },
                mediaPath = incoming.mediaPath.ifBlank { existing.mediaPath },
                mediaMime = incoming.mediaMime.ifBlank { existing.mediaMime },
                quotedId = incoming.quotedId.ifBlank { existing.quotedId },
                reactionsJson = if (existing.reactionsJson != "{}") existing.reactionsJson else incoming.reactionsJson,
                receiptStatus = finalReceipt,
                isViewOnce = existing.isViewOnce || incoming.isViewOnce,
                rawJson = incoming.rawJson.ifBlank { existing.rawJson },
            )
        )
    }

    @Transaction
    suspend fun replaceOptimisticMessage(localId: String, serverId: String, serverTs: Long) {
        val existing = getById(localId) ?: return
        if (serverId.isNotBlank() && serverId != localId) {
            deleteById(localId)
            insertOrReplace(
                existing.copy(
                    id = serverId,
                    ts = if (serverTs > 0) serverTs else existing.ts,
                    receiptStatus = "sent",
                )
            )
        } else {
            insertOrReplace(existing.copy(receiptStatus = "sent"))
        }
    }

    @Transaction
    suspend fun markMessageDeleted(msgId: String, fallbackChatId: String = ""): String {
        val existing = getById(msgId) ?: return normalizeJid(fallbackChatId)
        val orig = existing.originalBody.ifBlank { existing.body }
        insertOrReplace(existing.copy(isDeleted = true, originalBody = orig))
        return existing.chatId
    }

    @Transaction
    suspend fun markMessageEdited(msgId: String, newBody: String, fallbackChatId: String = ""): String {
        val existing = getById(msgId) ?: return normalizeJid(fallbackChatId)
        val orig = existing.originalBody.ifBlank { existing.body }
        insertOrReplace(existing.copy(isEdited = true, originalBody = orig, body = newBody))
        return existing.chatId
    }

    @Transaction
    suspend fun addReaction(msgId: String, senderIdRaw: String, emoji: String): String {
        val existing = getById(msgId) ?: return ""
        val senderClean = normalizeJid(senderIdRaw)
        val obj = runCatching { JSONObject(existing.reactionsJson.ifBlank { "{}" }) }.getOrElse { JSONObject() }
        if (emoji.isNotEmpty()) {
            obj.put(senderClean, emoji)
        } else {
            obj.remove(senderClean)
        }
        insertOrReplace(existing.copy(reactionsJson = obj.toString()))
        return existing.chatId
    }

    @Transaction
    suspend fun updateReceipt(chatIdRaw: String, msgIds: List<String>, status: String) {
        if (msgIds.isEmpty()) return
        val chatId = normalizeJid(chatIdRaw)
        val newRank = ReceiptRank.rankOf(status)
        for (mid in msgIds) {
            val row = getById(mid) ?: continue
            if (row.chatId == chatId && newRank > ReceiptRank.rankOf(row.receiptStatus)) {
                insertOrReplace(row.copy(receiptStatus = status))
            }
        }
    }
}

@Dao
interface ContactDao {
    @Query(
        """
        SELECT * FROM contacts
        ORDER BY CASE WHEN full_name != '' THEN full_name WHEN push_name != '' THEN push_name ELSE jid END COLLATE NOCASE ASC
        """
    )
    fun observeContacts(): Flow<List<ContactEntity>>

    @Query("SELECT COUNT(*) FROM contacts")
    fun observeCount(): Flow<Int>

    @Query("SELECT * FROM contacts WHERE jid = :jid LIMIT 1")
    suspend fun getByJid(jid: String): ContactEntity?

    @Query("UPDATE contacts SET avatar_path = :avatarPath, updated_at = :now WHERE jid = :jid")
    suspend fun updateAvatarPath(jid: String, avatarPath: String, now: Long = System.currentTimeMillis() / 1000L)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertOrReplace(contact: ContactEntity)

    @Transaction
    suspend fun upsertContact(
        jidRaw: String,
        phone: String = "",
        pushName: String = "",
        fullName: String = "",
        businessName: String = "",
        about: String = "",
        avatarPath: String = "",
    ) {
        val jid = normalizeJid(jidRaw)
        if (jid.isBlank()) return
        val resolvedPhone = phone.ifBlank {
            if ('@' in jid && !jid.endsWith("@g.us")) jid.substringBefore('@') else ""
        }
        val now = System.currentTimeMillis() / 1000L
        val existing = getByJid(jid)
        if (existing == null) {
            insertOrReplace(
                ContactEntity(
                    jid = jid,
                    phone = resolvedPhone,
                    pushName = pushName,
                    fullName = fullName,
                    businessName = businessName,
                    about = about,
                    avatarPath = avatarPath,
                    updatedAt = now,
                )
            )
            return
        }
        insertOrReplace(
            existing.copy(
                phone = resolvedPhone.ifBlank { existing.phone },
                pushName = pushName.ifBlank { existing.pushName },
                fullName = fullName.ifBlank { existing.fullName },
                businessName = businessName.ifBlank { existing.businessName },
                about = about.ifBlank { existing.about },
                avatarPath = avatarPath.ifBlank { existing.avatarPath },
                updatedAt = now,
            )
        )
    }

    @Transaction
    suspend fun setPresence(jidRaw: String, isOnline: Boolean, lastSeenTs: Long?) {
        val jid = normalizeJid(jidRaw)
        if (jid.isBlank()) return
        val existing = getByJid(jid)
        val now = System.currentTimeMillis() / 1000L
        if (existing == null) {
            upsertContact(jid)
        }
        val row = getByJid(jid) ?: return
        insertOrReplace(
            row.copy(
                isOnline = isOnline,
                isTyping = if (!isOnline) false else row.isTyping,
                lastSeenTs = lastSeenTs?.takeIf { it > 0L } ?: row.lastSeenTs,
                updatedAt = now,
            )
        )
    }

    @Transaction
    suspend fun setTyping(jidRaw: String, isTyping: Boolean) {
        val jid = normalizeJid(jidRaw)
        if (jid.isBlank()) return
        if (getByJid(jid) == null) {
            upsertContact(jid)
        }
        val row = getByJid(jid) ?: return
        insertOrReplace(row.copy(isTyping = isTyping, updatedAt = System.currentTimeMillis() / 1000L))
    }
}

@Dao
interface CallLogDao {
    @Query("SELECT * FROM call_log ORDER BY started_at DESC")
    fun observeCallLogs(): Flow<List<CallLogEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(entry: CallLogEntity)
}
