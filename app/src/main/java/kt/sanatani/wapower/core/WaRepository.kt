package kt.sanatani.wapower.core

import android.content.Context
import android.util.Base64
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kt.sanatani.wapower.db.ChatListRow
import kt.sanatani.wapower.db.ContactEntity
import kt.sanatani.wapower.db.MessageEntity
import kt.sanatani.wapower.db.WaDatabase
import kt.sanatani.wapower.db.normalizeJid
import kt.sanatani.wapower.jni.NeonizeBridge
import kt.sanatani.wapower.proto.NeonizeProtos
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.ExtendedTextMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.Message as E2EMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.ProtocolMessage
import org.json.JSONObject
import java.io.File
import java.util.UUID

class WaRepository(
    context: Context,
    private val sessionUuid: String,
    private val onLog: (String) -> Unit,
) : DefaultLifecycleObserver {

    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val db: WaDatabase = WaDatabase.getInstance(appContext)
    val ghostPolicy = GhostPolicy(appContext)

    @Volatile
    private var appInForeground = true

    @Volatile
    private var resyncDoneThisSession = false

    private var bgIdleJob: Job? = null

    val syncMachine = SyncStateMachine(
        scope = scope,
        fallbackDelayMs = 3500L,
        onReadyCallback = { onSessionReady() },
    )

    val syncState: StateFlow<SyncState> = syncMachine.state
    val ghostConfig: StateFlow<GhostConfig> = ghostPolicy.config

    val activeChats: Flow<List<ChatListRow>> = db.chatDao().observeActiveChats()
    val archivedChats: Flow<List<ChatListRow>> = db.chatDao().observeArchivedChats()
    val contacts: Flow<List<ContactEntity>> = db.contactDao().observeContacts()
    val chatCount: Flow<Int> = db.chatDao().observeCount()
    val messageCount: Flow<Int> = db.messageDao().observeCount()
    val contactCount: Flow<Int> = db.contactDao().observeCount()

    private val outboundQueue = OutboundQueue(
        scope = scope,
        awaitReady = { syncMachine.awaitReady() && NeonizeBridge.isConnected(sessionUuid) },
        sendTyping = { chatJid, composing -> sendTypingIfAllowed(chatJid, composing) },
        sendActual = { job -> performSend(job) },
    )

    init {
        outboundQueue.start()
        scope.launch(Dispatchers.Main) {
            ProcessLifecycleOwner.get().lifecycle.addObserver(this@WaRepository)
        }
        scope.launch {
            ghostPolicy.config.drop(1).collect { cfg ->
                if (syncState.value == SyncState.READY || syncState.value == SyncState.WS_CONNECTED) {
                    applyPresence(if (cfg.ghostPresence) false else appInForeground)
                }
            }
        }
    }

    override fun onStart(owner: LifecycleOwner) {
        bgIdleJob?.cancel()
        bgIdleJob = null
        appInForeground = true
        if (syncState.value == SyncState.READY && ghostPolicy.guard.allowPresence()) {
            scope.launch { applyPresence(true) }
        }
    }

    override fun onStop(owner: LifecycleOwner) {
        appInForeground = false
        bgIdleJob?.cancel()
        bgIdleJob = scope.launch {
            delay(4000L)
            if (!appInForeground) {
                applyPresence(false)
            }
        }
    }

    fun onConnecting() {
        resyncDoneThisSession = false
        syncMachine.onConnecting()
    }

    fun onStopped() {
        resyncDoneThisSession = false
        syncMachine.onStopped()
    }

    fun onConnected() {
        syncMachine.onWsConnected()
    }

    suspend fun handleProtoEvent(code: Int, payload: ByteArray) {
        when (code) {
            3 -> onConnected()
            6, 12 -> syncMachine.onConnecting()
            13 -> handleHistorySync(payload)
            17 -> handleMessageEvent(payload)
            18 -> handleReceiptEvent(payload)
            19 -> handleChatPresenceEvent(payload)
            20 -> handlePresenceEvent(payload)
            26 -> syncMachine.onOfflineSyncPreview()
            27 -> syncMachine.onOfflineSyncCompleted()
            52 -> handleDeleteForMeEvent(payload)
            56 -> syncMachine.onAppStateSyncComplete()
            58 -> handleContactSyncEvent(payload)
            59 -> handlePushNameEvent(payload)
        }
    }

    private suspend fun onSessionReady() {
        onLog("SyncState READY")
        applyPresence(null)
        if (!resyncDoneThisSession) {
            resyncDoneThisSession = true
            scope.launch { resyncContacts() }
        }
    }

    fun applyPresence(explicitOnline: Boolean?) {
        if (!NeonizeBridge.isConnected(sessionUuid)) return
        val wantOnline = if (!ghostPolicy.guard.allowPresence()) {
            false
        } else {
            explicitOnline ?: appInForeground
        }
        if (wantOnline) {
            NeonizeBridge.setPassive(sessionUuid, false)
            NeonizeBridge.sendPresence(sessionUuid, available = true)
        } else {
            NeonizeBridge.sendPresence(sessionUuid, available = false)
        }
    }

    fun sendTypingIfAllowed(chatJid: String, composing: Boolean) {
        if (!ghostPolicy.guard.allowTyping()) return
        if (!NeonizeBridge.isConnected(sessionUuid)) return
        NeonizeBridge.sendChatPresence(sessionUuid, chatJid, composing = composing, isAudio = false)
    }

    suspend fun markChatRead(chatJidRaw: String, force: Boolean = false) {
        val chatJid = normalizeJid(chatJidRaw)
        if (chatJid.isBlank()) return
        if (!force && !ghostPolicy.guard.allowMarkRead()) {
            onLog("Ghost Read suppressed MarkRead for $chatJid")
            return
        }
        val refs = db.messageDao().getUnreadIncomingRefs(chatJid)
        if (refs.isNotEmpty() && NeonizeBridge.isConnected(sessionUuid)) {
            val grouped = refs.groupBy { it.senderId.ifBlank { chatJid } }
            for ((sender, items) in grouped) {
                NeonizeBridge.markRead(
                    uuid = sessionUuid,
                    messageIds = items.map { it.id },
                    chatJid = chatJid,
                    senderJid = sender,
                    receiptType = "read",
                )
            }
        }
        db.messageDao().markIncomingReadLocal(chatJid)
        db.chatDao().clearUnread(chatJid)
    }

    suspend fun sendTextMessage(chatJidRaw: String, text: String, quotedId: String = ""): Boolean {
        val chatJid = normalizeJid(chatJidRaw)
        if (chatJid.isBlank() || text.isBlank()) return false
        val localId = "local_${System.currentTimeMillis()}_${UUID.randomUUID().toString().take(6)}"
        val nowSec = System.currentTimeMillis() / 1000L

        db.messageDao().upsertMessage(
            MessageEntity(
                id = localId,
                chatId = chatJid,
                senderId = "",
                fromMe = true,
                ts = nowSec,
                msgType = "text",
                body = text,
                quotedId = quotedId,
                receiptStatus = "pending",
            )
        )
        db.chatDao().upsertChat(
            jidRaw = chatJid,
            isGroup = chatJid.endsWith("@g.us"),
            lastMsgId = localId,
            lastPreview = text,
            lastTs = nowSec,
            unreadDelta = 0,
        )

        return outboundQueue.enqueue(
            OutboundJob(
                chatJid = chatJid,
                text = text,
                localMsgId = localId,
                quotedId = quotedId,
            )
        )
    }

    private suspend fun performSend(job: OutboundJob) {
        val msg = if (job.quotedId.isNotBlank()) {
            E2EMessage.newBuilder()
                .setExtendedTextMessage(
                    ExtendedTextMessage.newBuilder()
                        .setText(job.text)
                        .build()
                )
                .build()
        } else {
            E2EMessage.newBuilder()
                .setConversation(job.text)
                .build()
        }

        val res = NeonizeBridge.sendMessage(sessionUuid, job.chatJid, msg)
        res.onSuccess { sendResp ->
            val serverId = sendResp.id
            val ts = MessagePayloadExtractor.normalizeTimestampSec(sendResp.timestamp)
            db.messageDao().replaceOptimisticMessage(job.localMsgId, serverId, ts)
            db.chatDao().upsertChat(
                jidRaw = job.chatJid,
                lastMsgId = serverId.ifBlank { job.localMsgId },
                lastPreview = job.text,
                lastTs = if (ts > 0) ts else System.currentTimeMillis() / 1000L,
            )
        }.onFailure { err ->
            onLog("SendMessage failed (${job.chatJid}): ${err.message}")
            db.messageDao().getById(job.localMsgId)?.let { cur ->
                db.messageDao().insertOrReplace(cur.copy(receiptStatus = "failed"))
            }
        }
    }

    suspend fun resyncContacts(): Int {
        if (!NeonizeBridge.isConnected(sessionUuid)) return 0
        var count = 0
        NeonizeBridge.getAllContacts(sessionUuid).onSuccess { list ->
            for (ct in list) {
                val jid = NeonizeBridge.jidToString(ct.jid)
                if (jid.isBlank()) continue
                val info = ct.info
                val full = info.fullName.ifBlank { info.firstName }.trim()
                val phone = if ('@' in jid && !jid.endsWith("@g.us")) {
                    jid.substringBefore('@')
                } else {
                    info.redactedPhone
                }
                db.contactDao().upsertContact(
                    jidRaw = jid,
                    phone = phone,
                    pushName = info.pushName.trim(),
                    fullName = full,
                    businessName = info.businessName.trim(),
                )
                count++
            }
        }

        NeonizeBridge.getJoinedGroups(sessionUuid).onSuccess { groups ->
            for (grp in groups) {
                val gJid = NeonizeBridge.jidToString(grp.jid)
                if (gJid.isBlank()) continue
                val gName = grp.groupName.name.trim()
                db.contactDao().upsertContact(jidRaw = gJid, fullName = gName)
                db.chatDao().upsertChat(jidRaw = gJid, name = gName.ifBlank { null }, isGroup = true)
                count++
            }
        }

        onLog("Resynced $count contacts/groups")
        return count
    }

    fun updateGhost(transform: (GhostConfig) -> GhostConfig) {
        scope.launch {
            ghostPolicy.update(transform)
        }
    }

    private suspend fun handleHistorySync(payload: ByteArray) {
        val ev = runCatching { NeonizeProtos.HistorySync.parseFrom(payload) }.getOrNull() ?: return
        val data = ev.data
        for (pn in data.pushnamesList) {
            val jid = normalizeJid(pn.id)
            val name = pn.pushname.trim()
            if (jid.isNotBlank() && name.isNotBlank()) {
                db.contactDao().upsertContact(jidRaw = jid, pushName = name)
            }
        }

        var msgCount = 0
        for (conv in data.conversationsList) {
            val chatJid = normalizeJid(conv.id)
            if (chatJid.isBlank() || chatJid == "status@broadcast") continue

            val convName = conv.name.ifBlank { conv.displayName }.trim()
            val convLastTs = MessagePayloadExtractor.normalizeTimestampSec(
                if (conv.lastMsgTimestamp > 0) conv.lastMsgTimestamp else conv.conversationTimestamp
            )
            var lastTs = 0L
            var lastMid = ""
            var lastPrev = ""

            for (hsm in conv.messagesList) {
                val wmi = if (hsm.hasMessage()) hsm.message else continue
                val key = wmi.key
                val msgId = key.id
                if (msgId.isBlank()) continue

                val fromMe = key.fromMe
                val remoteJid = normalizeJid(key.remoteJID.ifBlank { chatJid }).ifBlank { chatJid }
                val participant = normalizeJid(key.participant.ifBlank { wmi.participant })
                val senderId = participant.ifBlank { if (fromMe) "" else remoteJid }
                val tsVal = MessagePayloadExtractor.normalizeTimestampSec(wmi.messageTimestamp)
                    .takeIf { it > 0 } ?: convLastTs
                val push = wmi.pushName.trim()

                if (push.isNotBlank() && senderId.isNotBlank() && !fromMe) {
                    db.contactDao().upsertContact(jidRaw = senderId, pushName = push)
                }
                if (!wmi.hasMessage()) continue
                val m = wmi.message

                if (m.hasProtocolMessage()) {
                    val proto = m.protocolMessage
                    val targetId = proto.key.id
                    if (targetId.isNotBlank()) {
                        when (proto.type) {
                            ProtocolMessage.Type.REVOKE -> db.messageDao().markMessageDeleted(targetId, remoteJid)
                            ProtocolMessage.Type.MESSAGE_EDIT -> {
                                val edited = MessagePayloadExtractor.extractText(proto.editedMessage)
                                if (edited.isNotBlank()) {
                                    db.messageDao().markMessageEdited(targetId, edited, remoteJid)
                                }
                            }
                            else -> Unit
                        }
                    }
                    continue
                }

                if (m.hasReactionMessage()) {
                    val rx = m.reactionMessage
                    val targetId = rx.key.id
                    if (targetId.isNotBlank()) {
                        db.messageDao().addReaction(targetId, senderId.ifBlank { remoteJid }, rx.text)
                    }
                    continue
                }

                val extracted = MessagePayloadExtractor.extractPayload(m) ?: continue
                val rawJson = serializeMediaRaw(extracted.mediaProto)
                db.messageDao().upsertMessage(
                    MessageEntity(
                        id = msgId,
                        chatId = remoteJid,
                        senderId = senderId,
                        fromMe = fromMe,
                        ts = tsVal,
                        msgType = extracted.msgType,
                        body = extracted.body,
                        mediaMime = extracted.mediaMime,
                        quotedId = extracted.quotedId,
                        receiptStatus = if (fromMe) "sent" else "delivered",
                        isViewOnce = extracted.isViewOnce,
                        rawJson = rawJson,
                    )
                )
                msgCount++
                if (tsVal >= lastTs) {
                    lastTs = tsVal
                    lastMid = msgId
                    lastPrev = extracted.preview
                }
            }

            db.chatDao().upsertChat(
                jidRaw = chatJid,
                name = convName.ifBlank { null },
                isGroup = chatJid.endsWith("@g.us"),
                lastMsgId = lastMid.ifBlank { null },
                lastPreview = lastPrev.ifBlank { null },
                lastTs = maxOf(lastTs, convLastTs),
                unreadCount = conv.unreadCount,
                mutedUntil = MessagePayloadExtractor.normalizeTimestampSec(conv.muteEndTime),
                pinned = conv.pinned > 0,
                archived = conv.archived,
            )
        }
        onLog("HistorySync ingested ${data.conversationsCount} chats, $msgCount msgs")
    }

    private suspend fun handleContactSyncEvent(payload: ByteArray) {
        val ev = runCatching { NeonizeProtos.ContactSync.parseFrom(payload) }.getOrNull() ?: return
        val jid = NeonizeBridge.jidToString(ev.jid)
        if (jid.isBlank()) return
        val act = if (ev.hasAction()) ev.action else null
        val fullName = act?.fullName.orEmpty().trim()
        val firstName = act?.firstName.orEmpty().trim()
        val pnJid = normalizeJid(act?.pnJID)
        val bestName = fullName.ifBlank { firstName }
        val phone = when {
            '@' in pnJid -> pnJid.substringBefore('@')
            '@' in jid && !jid.endsWith("@g.us") -> jid.substringBefore('@')
            else -> ""
        }
        db.contactDao().upsertContact(jidRaw = jid, phone = phone, fullName = bestName)
        if (pnJid.isNotBlank() && pnJid != jid) {
            db.contactDao().upsertContact(jidRaw = pnJid, phone = phone, fullName = bestName)
        }
    }

    private suspend fun handlePushNameEvent(payload: ByteArray) {
        val ev = runCatching { NeonizeProtos.PushName.parseFrom(payload) }.getOrNull() ?: return
        val jid = NeonizeBridge.jidToString(ev.jid)
        val newName = ev.newPushName.trim()
        if (jid.isNotBlank() && newName.isNotBlank()) {
            db.contactDao().upsertContact(jidRaw = jid, pushName = newName)
        }
    }

    private suspend fun handleMessageEvent(payload: ByteArray) {
        val ev = runCatching { NeonizeProtos.Message.parseFrom(payload) }.getOrNull() ?: return
        if (!ev.hasMessage()) return
        val info = ev.info
        val src = info.messageSource
        val chatId = NeonizeBridge.jidToString(src.chat)
        val senderId = NeonizeBridge.jidToString(src.sender)
        val fromMe = src.isFromMe
        val isGroup = src.isGroup
        val msgId = info.id
        val tsVal = MessagePayloadExtractor.normalizeTimestampSec(info.timestamp)
            .takeIf { it > 0 } ?: (System.currentTimeMillis() / 1000L)
        val pushName = info.pushname.trim()

        if (pushName.isNotBlank() && !fromMe) {
            if (senderId.isNotBlank()) {
                db.contactDao().upsertContact(jidRaw = senderId, pushName = pushName)
            }
            if (!isGroup && chatId.isNotBlank() && chatId != senderId) {
                db.contactDao().upsertContact(jidRaw = chatId, pushName = pushName)
            }
        }

        val m = ev.message
        if (m.hasProtocolMessage()) {
            val proto = m.protocolMessage
            val targetId = proto.key.id
            if (targetId.isNotBlank()) {
                when (proto.type) {
                    ProtocolMessage.Type.REVOKE -> db.messageDao().markMessageDeleted(targetId, chatId)
                    ProtocolMessage.Type.MESSAGE_EDIT -> {
                        val edited = MessagePayloadExtractor.extractText(proto.editedMessage)
                        if (edited.isNotBlank()) {
                            db.messageDao().markMessageEdited(targetId, edited, chatId)
                        }
                    }
                    else -> Unit
                }
            }
            return
        }

        if (m.hasReactionMessage()) {
            val rx = m.reactionMessage
            val targetId = rx.key.id
            if (targetId.isNotBlank()) {
                db.messageDao().addReaction(targetId, senderId.ifBlank { chatId }, rx.text)
            }
            return
        }

        val extracted = MessagePayloadExtractor.extractPayload(m) ?: return
        if (msgId.isBlank() || chatId.isBlank()) return

        var mediaPath = ""
        if (extracted.isViewOnce && extracted.mediaProto != null) {
            // Ghost View-Once: decrypt directly via DownloadAny without sending view-once opened receipt
            mediaPath = saveViewOnceInline(msgId, extracted)
        }

        db.messageDao().upsertMessage(
            MessageEntity(
                id = msgId,
                chatId = chatId,
                senderId = senderId,
                fromMe = fromMe,
                ts = tsVal,
                msgType = extracted.msgType,
                body = extracted.body,
                mediaPath = mediaPath,
                mediaMime = extracted.mediaMime,
                quotedId = extracted.quotedId,
                receiptStatus = if (fromMe) "sent" else "delivered",
                isViewOnce = extracted.isViewOnce,
                rawJson = serializeMediaRaw(extracted.mediaProto),
            )
        )
        db.chatDao().upsertChat(
            jidRaw = chatId,
            name = if (!isGroup && !fromMe && pushName.isNotBlank()) pushName else null,
            isGroup = isGroup,
            lastMsgId = msgId,
            lastPreview = extracted.preview,
            lastTs = tsVal,
            unreadDelta = if (fromMe) 0 else 1,
        )
    }

    private suspend fun handleReceiptEvent(payload: ByteArray) {
        val ev = runCatching { NeonizeProtos.Receipt.parseFrom(payload) }.getOrNull() ?: return
        val chatJid = NeonizeBridge.jidToString(ev.messageSource.chat)
        val msgIds = ev.messageIDsList.filter { it.isNotBlank() }
        if (chatJid.isBlank() || msgIds.isEmpty()) return

        val status = when (ev.type) {
            NeonizeProtos.Receipt.ReceiptType.DELIVERED -> "delivered"
            NeonizeProtos.Receipt.ReceiptType.READ,
            NeonizeProtos.Receipt.ReceiptType.READ_SELF -> "read"
            NeonizeProtos.Receipt.ReceiptType.PLAYED,
            NeonizeProtos.Receipt.ReceiptType.PLAYED_SELF -> "played"
            else -> return
        }
        db.messageDao().updateReceipt(chatJid, msgIds, status)
        if (ev.type == NeonizeProtos.Receipt.ReceiptType.READ_SELF) {
            db.messageDao().markIncomingReadLocal(chatJid)
            db.chatDao().clearUnread(chatJid)
        }
    }

    private suspend fun handlePresenceEvent(payload: ByteArray) {
        val ev = runCatching { NeonizeProtos.Presence.parseFrom(payload) }.getOrNull() ?: return
        val jid = NeonizeBridge.jidToString(ev.from)
        if (jid.isBlank()) return
        val lastSeen = MessagePayloadExtractor.normalizeTimestampSec(ev.lastSeen)
        db.contactDao().setPresence(
            jidRaw = jid,
            isOnline = !ev.unavailable,
            lastSeenTs = lastSeen.takeIf { it > 0 },
        )
    }

    private suspend fun handleChatPresenceEvent(payload: ByteArray) {
        val ev = runCatching { NeonizeProtos.ChatPresence.parseFrom(payload) }.getOrNull() ?: return
        val chatJid = NeonizeBridge.jidToString(ev.messageSource.chat)
        if (chatJid.isBlank()) return
        val isTyping = ev.state == NeonizeProtos.ChatPresence.ChatPresenceState.COMPOSING
        db.contactDao().setTyping(chatJid, isTyping)
    }

    private suspend fun handleDeleteForMeEvent(payload: ByteArray) {
        val ev = runCatching { NeonizeProtos.MessageDeleteForMe.parseFrom(payload) }.getOrNull() ?: return
        val chatJid = NeonizeBridge.jidToString(ev.chatJID)
        if (ev.messageID.isNotBlank()) {
            db.messageDao().markMessageDeleted(ev.messageID, chatJid)
        }
    }

    private fun saveViewOnceInline(msgId: String, payload: ExtractedMessagePayload): String {
        val proto = payload.mediaProto ?: return ""
        val bytes = NeonizeBridge.downloadAny(sessionUuid, proto).getOrNull() ?: return ""
        if (bytes.isEmpty()) return ""
        return runCatching {
            val dir = File(appContext.filesDir, "view_once_inline").apply { mkdirs() }
            val ext = MessagePayloadExtractor.extForMedia(payload.msgType, payload.mediaMime, payload.fileName)
            val safeId = msgId.filter { it.isLetterOrDigit() || it == '-' || it == '_' }.take(64).ifBlank {
                UUID.randomUUID().toString().take(12)
            }
            val file = File(dir, "vo_$safeId$ext")
            file.writeBytes(bytes)
            file.absolutePath
        }.getOrDefault("")
    }

    private fun serializeMediaRaw(mediaProto: E2EMessage?): String {
        if (mediaProto == null) return ""
        return runCatching {
            val b64 = Base64.encodeToString(mediaProto.toByteArray(), Base64.NO_WRAP)
            JSONObject().put("pb_b64", b64).toString()
        }.getOrDefault("")
    }
}
