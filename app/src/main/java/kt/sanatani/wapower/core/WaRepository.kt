package kt.sanatani.wapower.core

import android.content.Context
import android.net.Uri
import android.util.Base64
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import com.google.protobuf.ByteString
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kt.sanatani.wapower.db.ChatListRow
import kt.sanatani.wapower.db.ContactEntity
import kt.sanatani.wapower.db.MessageEntity
import kt.sanatani.wapower.db.WaDatabase
import kt.sanatani.wapower.db.normalizeJid
import kt.sanatani.wapower.jni.NeonizeBridge
import kt.sanatani.wapower.proto.NeonizeProtos
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.AudioMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.ContextInfo
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.DocumentMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.ExtendedTextMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.ImageMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.Message as E2EMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.ProtocolMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.VideoMessage
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

data class WhatsAppCheckResult(
    val ok: Boolean,
    val registered: Boolean,
    val jid: String = "",
    val phone: String = "",
    val error: String = "",
)

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

    private val avatarLookupAttempted = ConcurrentHashMap.newKeySet<String>()

    fun observeChat(chatJid: String): Flow<ChatListRow?> =
        db.chatDao().observeChat(normalizeJid(chatJid))

    fun observeMessages(chatJid: String): Flow<List<MessageEntity>> =
        db.messageDao().observeMessages(normalizeJid(chatJid))

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
            val quotedRow = db.messageDao().getById(job.quotedId)
            val ctxBuilder = ContextInfo.newBuilder()
                .setStanzaID(job.quotedId)
            if (quotedRow != null) {
                val participant = when {
                    quotedRow.senderId.isNotBlank() -> quotedRow.senderId
                    quotedRow.fromMe -> NeonizeBridge.getMe(sessionUuid)?.let {
                        if (it.hasJID()) NeonizeBridge.jidToString(it.jid) else ""
                    }.orEmpty()
                    else -> job.chatJid
                }
                if (participant.isNotBlank()) {
                    ctxBuilder.setParticipant(participant)
                }
                ctxBuilder.setQuotedMessage(
                    E2EMessage.newBuilder().setConversation(quotedRow.body).build()
                )
            }
            E2EMessage.newBuilder()
                .setExtendedTextMessage(
                    ExtendedTextMessage.newBuilder()
                        .setText(job.text)
                        .setContextInfo(ctxBuilder.build())
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

    suspend fun sendReaction(chatJidRaw: String, messageId: String, senderJidRaw: String, emoji: String) {
        val chatJid = normalizeJid(chatJidRaw)
        if (chatJid.isBlank() || messageId.isBlank()) return
        val meJid = NeonizeBridge.getMe(sessionUuid)?.let {
            if (it.hasJID()) NeonizeBridge.jidToString(it.jid) else ""
        }.orEmpty()
        val effectiveSender = normalizeJid(senderJidRaw).ifBlank { chatJid }
        val built = NeonizeBridge.buildReaction(
            uuid = sessionUuid,
            chatJid = chatJid,
            senderJid = effectiveSender,
            messageId = messageId,
            reaction = emoji,
        )
        built.onSuccess { reactionMsg ->
            NeonizeBridge.sendMessage(sessionUuid, chatJid, reactionMsg).onSuccess {
                db.messageDao().addReaction(messageId, meJid.ifBlank { "me" }, emoji)
            }.onFailure { err ->
                onLog("Send reaction failed: ${err.message}")
            }
        }.onFailure { err ->
            onLog("BuildReaction failed: ${err.message}")
        }
    }

    suspend fun sendMediaAttachment(
        chatJidRaw: String,
        uri: Uri,
        mimeType: String,
        fileName: String,
        caption: String = "",
    ): Result<String> = withContext(Dispatchers.IO) {
        val chatJid = normalizeJid(chatJidRaw)
        if (chatJid.isBlank()) return@withContext Result.failure(IllegalArgumentException("Invalid chat"))
        if (!syncMachine.awaitReady(5000L) || !NeonizeBridge.isConnected(sessionUuid)) {
            return@withContext Result.failure(IllegalStateException("WhatsApp not connected"))
        }

        val bytes = runCatching {
            appContext.contentResolver.openInputStream(uri)?.use { it.readBytes() }
        }.getOrNull() ?: return@withContext Result.failure(IllegalStateException("Failed to read file"))

        val lowerMime = mimeType.lowercase()
        val (msgType, mediaInt, defaultPreview) = when {
            lowerMime.startsWith("image/") -> Triple("image", 0, "Photo")
            lowerMime.startsWith("video/") -> Triple("video", 1, "Video")
            lowerMime.startsWith("audio/") -> Triple("audio", 2, "Audio")
            else -> Triple("document", 3, fileName.ifBlank { "Document" })
        }

        val upload = NeonizeBridge.upload(sessionUuid, bytes, mediaInt).getOrElse {
            return@withContext Result.failure(it)
        }

        val msgBuilder = E2EMessage.newBuilder()
        when (msgType) {
            "image" -> msgBuilder.setImageMessage(
                ImageMessage.newBuilder()
                    .setURL(upload.url)
                    .setDirectPath(upload.directPath)
                    .setMediaKey(upload.mediaKey)
                    .setFileEncSHA256(upload.fileEncSHA256)
                    .setFileSHA256(upload.fileSHA256)
                    .setFileLength(upload.fileLength.toLong())
                    .setMimetype(mimeType.ifBlank { "image/jpeg" })
                    .setCaption(caption)
                    .build()
            )
            "video" -> msgBuilder.setVideoMessage(
                VideoMessage.newBuilder()
                    .setURL(upload.url)
                    .setDirectPath(upload.directPath)
                    .setMediaKey(upload.mediaKey)
                    .setFileEncSHA256(upload.fileEncSHA256)
                    .setFileSHA256(upload.fileSHA256)
                    .setFileLength(upload.fileLength.toLong())
                    .setMimetype(mimeType.ifBlank { "video/mp4" })
                    .setCaption(caption)
                    .build()
            )
            "audio" -> msgBuilder.setAudioMessage(
                AudioMessage.newBuilder()
                    .setURL(upload.url)
                    .setDirectPath(upload.directPath)
                    .setMediaKey(upload.mediaKey)
                    .setFileEncSHA256(upload.fileEncSHA256)
                    .setFileSHA256(upload.fileSHA256)
                    .setFileLength(upload.fileLength.toLong())
                    .setMimetype(mimeType.ifBlank { "audio/ogg" })
                    .setPTT(false)
                    .build()
            )
            else -> msgBuilder.setDocumentMessage(
                DocumentMessage.newBuilder()
                    .setURL(upload.url)
                    .setDirectPath(upload.directPath)
                    .setMediaKey(upload.mediaKey)
                    .setFileEncSHA256(upload.fileEncSHA256)
                    .setFileSHA256(upload.fileSHA256)
                    .setFileLength(upload.fileLength.toLong())
                    .setMimetype(mimeType.ifBlank { "application/octet-stream" })
                    .setFileName(fileName.ifBlank { "file" })
                    .setTitle(fileName.ifBlank { "file" })
                    .setCaption(caption)
                    .build()
            )
        }

        val e2eMsg = msgBuilder.build()
        val resp = NeonizeBridge.sendMessage(sessionUuid, chatJid, e2eMsg).getOrElse {
            return@withContext Result.failure(it)
        }

        val msgId = resp.id.ifBlank { "local_${UUID.randomUUID().toString().take(8)}" }
        val ts = MessagePayloadExtractor.normalizeTimestampSec(resp.timestamp)
            .takeIf { it > 0 } ?: (System.currentTimeMillis() / 1000L)

        val localFile = runCatching {
            val dir = File(appContext.filesDir, "media_cache").apply { mkdirs() }
            val ext = MessagePayloadExtractor.extForMedia(msgType, mimeType, fileName)
            val safeId = msgId.filter { it.isLetterOrDigit() || it == '-' || it == '_' }.take(64)
            val out = File(dir, "sent_$safeId$ext")
            out.writeBytes(bytes)
            out.absolutePath
        }.getOrDefault("")

        val body = caption.ifBlank { defaultPreview }
        db.messageDao().upsertMessage(
            MessageEntity(
                id = msgId,
                chatId = chatJid,
                fromMe = true,
                ts = ts,
                msgType = msgType,
                body = body,
                mediaPath = localFile,
                mediaMime = mimeType,
                receiptStatus = "sent",
                rawJson = serializeMediaRaw(e2eMsg),
            )
        )
        db.chatDao().upsertChat(
            jidRaw = chatJid,
            isGroup = chatJid.endsWith("@g.us"),
            lastMsgId = msgId,
            lastPreview = if (caption.isNotBlank()) "$defaultPreview: $caption" else defaultPreview,
            lastTs = ts,
            unreadDelta = 0,
        )
        Result.success(msgId)
    }

    suspend fun downloadMessageMedia(msgId: String): Result<String> = withContext(Dispatchers.IO) {
        val row = db.messageDao().getById(msgId)
            ?: return@withContext Result.failure(IllegalStateException("Message not found"))
        if (row.mediaPath.isNotBlank() && File(row.mediaPath).exists()) {
            return@withContext Result.success(row.mediaPath)
        }
        val b64 = runCatching { JSONObject(row.rawJson).optString("pb_b64", "") }.getOrDefault("")
        if (b64.isBlank()) {
            return@withContext Result.failure(IllegalStateException("No downloadable media metadata"))
        }
        val proto = runCatching { E2EMessage.parseFrom(Base64.decode(b64, Base64.DEFAULT)) }.getOrNull()
            ?: return@withContext Result.failure(IllegalStateException("Corrupt media payload"))
        val bytes = NeonizeBridge.downloadAny(sessionUuid, proto).getOrElse {
            return@withContext Result.failure(it)
        }
        if (bytes.isEmpty()) {
            return@withContext Result.failure(IllegalStateException("Downloaded empty bytes"))
        }
        val dir = File(appContext.filesDir, "media_cache").apply { mkdirs() }
        val ext = MessagePayloadExtractor.extForMedia(row.msgType, row.mediaMime)
        val safeId = msgId.filter { it.isLetterOrDigit() || it == '-' || it == '_' }.take(64)
        val out = File(dir, "dl_$safeId$ext")
        out.writeBytes(bytes)
        db.messageDao().updateMediaPath(msgId, out.absolutePath, row.mediaMime)
        Result.success(out.absolutePath)
    }

    suspend fun setChatPinned(chatJidRaw: String, pinned: Boolean) {
        val jid = normalizeJid(chatJidRaw)
        db.chatDao().setPinned(jid, pinned)
        if (NeonizeBridge.isConnected(sessionUuid)) {
            NeonizeBridge.putPinned(sessionUuid, jid, pinned)
        }
    }

    suspend fun setChatArchived(chatJidRaw: String, archived: Boolean) {
        val jid = normalizeJid(chatJidRaw)
        db.chatDao().setArchived(jid, archived)
        if (NeonizeBridge.isConnected(sessionUuid)) {
            NeonizeBridge.putArchived(sessionUuid, jid, archived)
        }
    }

    suspend fun setChatMuted(chatJidRaw: String, muted: Boolean) {
        val jid = normalizeJid(chatJidRaw)
        val untilSec = if (muted) (System.currentTimeMillis() / 1000L) + 8 * 3600L else 0L
        db.chatDao().setMutedUntil(jid, untilSec)
        if (NeonizeBridge.isConnected(sessionUuid)) {
            NeonizeBridge.putMutedUntil(sessionUuid, jid, if (muted) 8 * 3600f else 0f)
        }
    }

    fun fetchProfileAvatarIfNeeded(jidRaw: String) {
        val jid = normalizeJid(jidRaw)
        if (jid.isBlank() || !avatarLookupAttempted.add(jid)) return
        scope.launch {
            if (!NeonizeBridge.isConnected(sessionUuid)) {
                avatarLookupAttempted.remove(jid)
                return@launch
            }
            NeonizeBridge.getProfilePictureUrl(sessionUuid, jid, preview = true).onSuccess { url ->
                if (url.isNotBlank()) {
                    db.contactDao().updateAvatarPath(jid, url)
                    db.chatDao().updateAvatarPath(jid, url)
                }
            }
        }
    }

    fun subscribeChatPresence(jidRaw: String) {
        val jid = normalizeJid(jidRaw)
        if (jid.isBlank() || jid.endsWith("@g.us")) return
        scope.launch {
            if (NeonizeBridge.isConnected(sessionUuid)) {
                NeonizeBridge.subscribePresence(sessionUuid, jid)
            }
        }
    }

    suspend fun checkOnWhatsApp(phoneInput: String): WhatsAppCheckResult = withContext(Dispatchers.IO) {
        val clean = NeonizeBridge.sanitizePhoneDigits(phoneInput)
        if (clean.length < 5) {
            return@withContext WhatsAppCheckResult(ok = false, registered = false, phone = clean, error = "Enter valid phone digits")
        }
        if (!NeonizeBridge.isConnected(sessionUuid)) {
            return@withContext WhatsAppCheckResult(ok = false, registered = false, phone = clean, error = "WhatsApp not connected")
        }
        val res = NeonizeBridge.isOnWhatsApp(sessionUuid, listOf(clean)).getOrElse {
            return@withContext WhatsAppCheckResult(ok = false, registered = false, phone = clean, error = it.message ?: "Check failed")
        }
        val first = res.firstOrNull()
        val isIn = first?.isIn == true
        val jid = first?.let { NeonizeBridge.jidToString(it.jid) }.orEmpty().ifBlank {
            if (isIn) "$clean@s.whatsapp.net" else ""
        }
        if (isIn && jid.isNotBlank()) {
            db.contactDao().upsertContact(jidRaw = jid, phone = clean)
        }
        WhatsAppCheckResult(ok = true, registered = isIn, jid = jid, phone = clean)
    }

    suspend fun placeCall(targetJid: String, isVideo: Boolean): Result<String> = withContext(Dispatchers.IO) {
        if (!NeonizeBridge.isConnected(sessionUuid)) {
            return@withContext Result.failure(IllegalStateException("WhatsApp not connected"))
        }
        val raw = NeonizeBridge.callPlace(sessionUuid, targetJid, isVideo)
        val obj = runCatching { JSONObject(raw) }.getOrNull()
        if (obj?.optBoolean("ok") == true) {
            val cid = obj.optString("call_id", "")
            onLog("Placed ${if (isVideo) "video" else "voice"} call to $targetJid ($cid)")
            Result.success(cid)
        } else {
            val err = obj?.optString("error", "Call failed") ?: "Call failed"
            onLog("CallPlace failed: $err")
            Result.failure(IllegalStateException(err))
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
