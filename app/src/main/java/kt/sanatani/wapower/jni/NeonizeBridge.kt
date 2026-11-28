package kt.sanatani.wapower.jni

import kt.sanatani.wapower.db.normalizeJid
import kt.sanatani.wapower.proto.NeonizeProtos
import kt.sanatani.wapower.proto.waCompanionReg.WACompanionReg.DeviceProps
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

sealed interface NativeEvent {
    data class QrCode(val uuid: String, val code: String) : NativeEvent
    data class LoginStatus(val uuid: String, val status: String) : NativeEvent
    data class ProtoEvent(val uuid: String, val code: Int, val payload: ByteArray) : NativeEvent
    data class LogLine(val level: String, val name: String, val message: String) : NativeEvent
}

data class StoredDeviceInfo(
    val jid: String,
    val pushName: String,
    val businessName: String,
    val initialized: Boolean,
)

object NeonizeBridge {
    private val _nativeEvents = MutableSharedFlow<NativeEvent>(extraBufferCapacity = 128)
    val nativeEvents: SharedFlow<NativeEvent> = _nativeEvents.asSharedFlow()

    @Volatile
    private var librariesLoaded = false

    @Volatile
    private var initialized = false

    @Synchronized
    fun ensureInitialized(): Boolean {
        if (initialized) return true
        if (!librariesLoaded) {
            System.loadLibrary("neonize")
            System.loadLibrary("wa_jni")
            librariesLoaded = true
        }
        initialized = nativeInit(this)
        return initialized
    }

    fun getVersion(): String =
        if (ensureInitialized()) nativeGetVersion().orEmpty() else ""

    fun startSession(
        dbPath: String,
        uuid: String,
        jid: NeonizeProtos.JID? = null,
        newDevice: Boolean = false,
        logLevel: String = "INFO",
        subscribedEventCodes: ByteArray = DEFAULT_SUBSCRIBED_EVENTS,
        deviceDisplayName: String = "WA power (Android)",
    ): String? {
        if (!ensureInitialized()) return "Failed to load native JNI libraries"
        val props = DeviceProps.newBuilder()
            .setOs(deviceDisplayName)
            .setPlatformType(DeviceProps.PlatformType.SAFARI)
            .build()
            .toByteArray()
        val jidBytes = jid?.toByteArray() ?: ByteArray(0)
        return nativeStartNeonize(
            dbPath = dbPath,
            uuid = uuid,
            jidBytes = jidBytes,
            newDevice = if (newDevice) 1 else 0,
            logLevel = logLevel,
            subscribers = subscribedEventCodes,
            devicePropsBytes = props,
            pairPhoneBytes = ByteArray(0),
        )
    }

    fun pairPhone(
        uuid: String,
        phoneDigits: String,
        showPushNotification: Boolean = true,
        clientDisplayName: String = "Safari (Linux)",
        clientType: Int = 6,
    ): Result<String> {
        if (!ensureInitialized()) {
            return Result.failure(IllegalStateException("Native bridge not initialized"))
        }
        val cleanPhone = sanitizePhoneDigits(phoneDigits)
        if (cleanPhone.length < 7) {
            return Result.failure(IllegalArgumentException("Enter full phone number with country code (digits only)"))
        }
        val params = NeonizeProtos.PairPhoneParams.newBuilder()
            .setPhone(cleanPhone)
            .setShowPushNotification(showPushNotification)
            .setClientType(clientType)
            .setClientDisplayName(clientDisplayName)
            .build()
            .toByteArray()

        val respBytes = nativePairPhone(uuid, params)
            ?: return Result.failure(IllegalStateException("Empty response from PairPhone"))
        return runCatching {
            val resp = NeonizeProtos.PairPhoneReturnFunction.parseFrom(respBytes)
            if (resp.hasError() && resp.error.isNotBlank()) {
                error(resp.error)
            }
            resp.code
        }
    }

    fun stop(uuid: String) {
        if (ensureInitialized()) nativeStop(uuid)
    }

    fun stopAll() {
        if (librariesLoaded) nativeStopAll()
    }

    fun disconnect(uuid: String) {
        if (ensureInitialized()) nativeDisconnect(uuid)
    }

    fun logout(uuid: String): String? =
        if (ensureInitialized()) nativeLogout(uuid) else "Bridge not initialized"

    fun isConnected(uuid: String): Boolean =
        ensureInitialized() && nativeIsConnected(uuid)

    fun isLoggedIn(uuid: String): Boolean =
        ensureInitialized() && nativeIsLoggedIn(uuid)

    fun getMe(uuid: String): NeonizeProtos.Device? {
        if (!ensureInitialized()) return null
        val bytes = nativeGetMe(uuid) ?: return null
        if (bytes.isEmpty()) return null
        return runCatching { NeonizeProtos.Device.parseFrom(bytes) }.getOrNull()
    }

    fun getAllDevices(dbPath: String): List<StoredDeviceInfo> {
        if (!ensureInitialized()) return emptyList()
        val raw = nativeGetAllDevices(dbPath).orEmpty()
        return parseAllDevicesString(raw)
    }

    fun sendPresence(uuid: String, available: Boolean): String? {
        if (!ensureInitialized()) return "Bridge not initialized"
        return nativeSendPresence(uuid, if (available) "available" else "unavailable")
    }

    fun setPassive(uuid: String, passive: Boolean): String? {
        if (!ensureInitialized()) return "Bridge not initialized"
        return nativeSetPassive(uuid, passive)
    }

    fun buildJid(jidRaw: String): NeonizeProtos.JID {
        val norm = normalizeJid(jidRaw)
        val user = if ('@' in norm) norm.substringBefore('@') else norm
        val server = if ('@' in norm) norm.substringAfter('@') else "s.whatsapp.net"
        return NeonizeProtos.JID.newBuilder()
            .setUser(user)
            .setRawAgent(0)
            .setDevice(0)
            .setIntegrator(0)
            .setServer(server)
            .setIsEmpty(user.isBlank() && server.isBlank())
            .build()
    }

    fun jidToString(jid: NeonizeProtos.JID?): String {
        if (jid == null || jid.isEmpty) return ""
        val u = jid.user
        val s = jid.server
        if (u.isBlank() && s.isBlank()) return ""
        return if (u.isNotBlank() && s.isNotBlank()) normalizeJid("$u@$s") else (u.ifBlank { s })
    }

    fun sendMessage(
        uuid: String,
        toJid: String,
        message: WAWebProtobufsE2E.Message,
    ): Result<NeonizeProtos.SendResponse> {
        if (!ensureInitialized()) return Result.failure(IllegalStateException("Bridge not initialized"))
        val jidBytes = buildJid(toJid).toByteArray()
        val msgBytes = message.toByteArray()
        val respBytes = nativeSendMessage(uuid, jidBytes, msgBytes)
            ?: return Result.failure(IllegalStateException("Empty SendMessage response"))
        return runCatching {
            val ret = NeonizeProtos.SendMessageReturnFunction.parseFrom(respBytes)
            if (ret.hasError() && ret.error.isNotBlank()) error(ret.error)
            ret.sendResponse
        }
    }

    fun markRead(
        uuid: String,
        messageIds: List<String>,
        chatJid: String,
        senderJid: String,
        receiptType: String = "read",
        timestampSec: Int = (System.currentTimeMillis() / 1000L).toInt(),
    ): String? {
        if (!ensureInitialized() || messageIds.isEmpty()) return null
        return nativeMarkRead(
            uuid = uuid,
            idsSpaceJoined = messageIds.joinToString(" "),
            timestampSec = timestampSec,
            chatJidBytes = buildJid(chatJid).toByteArray(),
            senderJidBytes = buildJid(senderJid).toByteArray(),
            receiptType = receiptType,
        )
    }

    fun sendChatPresence(
        uuid: String,
        chatJid: String,
        composing: Boolean,
        isAudio: Boolean = false,
    ): String? {
        if (!ensureInitialized()) return "Bridge not initialized"
        val state = if (composing) 0 else 1
        val media = if (isAudio) 1 else 0
        return nativeSendChatPresence(uuid, buildJid(chatJid).toByteArray(), state, media)
    }

    fun getAllContacts(uuid: String): Result<List<NeonizeProtos.Contact>> {
        if (!ensureInitialized()) return Result.failure(IllegalStateException("Bridge not initialized"))
        val bytes = nativeGetAllContacts(uuid)
            ?: return Result.failure(IllegalStateException("Empty GetAllContacts response"))
        return runCatching {
            val ret = NeonizeProtos.ContactsGetAllContactsReturnFunction.parseFrom(bytes)
            if (ret.hasError() && ret.error.isNotBlank()) error(ret.error)
            ret.contactList
        }
    }

    fun getJoinedGroups(uuid: String): Result<List<NeonizeProtos.GroupInfo>> {
        if (!ensureInitialized()) return Result.failure(IllegalStateException("Bridge not initialized"))
        val bytes = nativeGetJoinedGroups(uuid)
            ?: return Result.failure(IllegalStateException("Empty GetJoinedGroups response"))
        return runCatching {
            val ret = NeonizeProtos.GetJoinedGroupsReturnFunction.parseFrom(bytes)
            if (ret.hasError() && ret.error.isNotBlank()) error(ret.error)
            ret.groupList
        }
    }

    fun downloadAny(uuid: String, message: WAWebProtobufsE2E.Message): Result<ByteArray> {
        if (!ensureInitialized()) return Result.failure(IllegalStateException("Bridge not initialized"))
        val bytes = nativeDownloadAny(uuid, message.toByteArray())
            ?: return Result.failure(IllegalStateException("Empty DownloadAny response"))
        return runCatching {
            val ret = NeonizeProtos.DownloadReturnFunction.parseFrom(bytes)
            if (ret.hasError() && ret.error.isNotBlank()) error(ret.error)
            ret.binary.toByteArray()
        }
    }

    fun subscribePresence(uuid: String, jid: String): String? {
        if (!ensureInitialized()) return "Bridge not initialized"
        return nativeSubscribePresence(uuid, buildJid(jid).toByteArray())
    }

    fun putPinned(uuid: String, jid: String, pinned: Boolean): String? =
        if (ensureInitialized()) nativePutPinned(uuid, buildJid(jid).toByteArray(), pinned) else "Bridge not initialized"

    fun putArchived(uuid: String, jid: String, archived: Boolean): String? =
        if (ensureInitialized()) nativePutArchived(uuid, buildJid(jid).toByteArray(), archived) else "Bridge not initialized"

    fun putMutedUntil(uuid: String, jid: String, seconds: Float): String? =
        if (ensureInitialized()) nativePutMutedUntil(uuid, buildJid(jid).toByteArray(), seconds) else "Bridge not initialized"

    fun buildReaction(
        uuid: String,
        chatJid: String,
        senderJid: String,
        messageId: String,
        reaction: String,
    ): Result<WAWebProtobufsE2E.Message> {
        if (!ensureInitialized()) return Result.failure(IllegalStateException("Bridge not initialized"))
        val bytes = nativeBuildReaction(
            uuid = uuid,
            chatJidBytes = buildJid(chatJid).toByteArray(),
            senderJidBytes = buildJid(senderJid).toByteArray(),
            messageId = messageId,
            reaction = reaction,
        ) ?: return Result.failure(IllegalStateException("Empty BuildReaction response"))
        return runCatching {
            val ret = NeonizeProtos.BuildMessageReturnFunction.parseFrom(bytes)
            if (ret.hasError() && ret.error.isNotBlank()) error(ret.error)
            ret.message
        }
    }

    fun isOnWhatsApp(uuid: String, numbers: List<String>): Result<List<NeonizeProtos.IsOnWhatsAppResponse>> {
        if (!ensureInitialized()) return Result.failure(IllegalStateException("Bridge not initialized"))
        val cleaned = numbers.map { sanitizePhoneDigits(it) }.filter { it.isNotBlank() }
        if (cleaned.isEmpty()) return Result.success(emptyList())
        val bytes = nativeIsOnWhatsApp(uuid, cleaned.joinToString(" "))
            ?: return Result.failure(IllegalStateException("Empty IsOnWhatsApp response"))
        return runCatching {
            val ret = NeonizeProtos.IsOnWhatsAppReturnFunction.parseFrom(bytes)
            if (ret.hasError() && ret.error.isNotBlank()) error(ret.error)
            ret.isOnWhatsAppResponseList
        }
    }

    fun getProfilePictureUrl(uuid: String, jid: String, preview: Boolean = true): Result<String> {
        if (!ensureInitialized()) return Result.failure(IllegalStateException("Bridge not initialized"))
        val params = NeonizeProtos.GetProfilePictureParams.newBuilder()
            .setPreview(preview)
            .build()
            .toByteArray()
        val bytes = nativeGetProfilePicture(uuid, buildJid(jid).toByteArray(), params)
            ?: return Result.failure(IllegalStateException("Empty GetProfilePicture response"))
        return runCatching {
            val ret = NeonizeProtos.GetProfilePictureReturnFunction.parseFrom(bytes)
            if (ret.hasError() && ret.error.isNotBlank()) error(ret.error)
            ret.picture.url
        }
    }

    fun upload(uuid: String, data: ByteArray, mediaType: Int): Result<NeonizeProtos.UploadResponse> {
        if (!ensureInitialized()) return Result.failure(IllegalStateException("Bridge not initialized"))
        val bytes = nativeUpload(uuid, data, mediaType)
            ?: return Result.failure(IllegalStateException("Empty Upload response"))
        return runCatching {
            val ret = NeonizeProtos.UploadReturnFunction.parseFrom(bytes)
            if (ret.hasError() && ret.error.isNotBlank()) error(ret.error)
            ret.uploadResponse
        }
    }

    fun callPlace(uuid: String, target: String, isVideo: Boolean): String =
        if (ensureInitialized()) nativeCallPlace(uuid, target, if (isVideo) 1 else 0).orEmpty() else "{\"ok\":false}"

    fun listActiveCallsJson(uuid: String): String =
        if (ensureInitialized()) nativeCallListActive(uuid).orEmpty() else "{}"

    // ---- Native JNI callbacks invoked by wa_jni.cpp (must keep exact signatures) ----

    @Suppress("unused")
    fun onNativeQr(uuid: String, qrCode: String) {
        _nativeEvents.tryEmit(NativeEvent.QrCode(uuid, qrCode))
    }

    @Suppress("unused")
    fun onNativeLoginStatus(uuid: String, status: String) {
        _nativeEvents.tryEmit(NativeEvent.LoginStatus(uuid, status))
    }

    @Suppress("unused")
    fun onNativeEvent(uuid: String, data: ByteArray, code: Int) {
        _nativeEvents.tryEmit(NativeEvent.ProtoEvent(uuid, code, data))
    }

    @Suppress("unused")
    fun onNativeLog(data: ByteArray) {
        if (data.isEmpty()) return
        val entry = runCatching { NeonizeProtos.LogEntry.parseFrom(data) }.getOrNull()
        if (entry != null) {
            _nativeEvents.tryEmit(
                NativeEvent.LogLine(
                    level = entry.level,
                    name = entry.name,
                    message = entry.message,
                )
            )
        } else {
            val raw = data.toString(Charsets.UTF_8).trim()
            if (raw.isNotEmpty()) {
                _nativeEvents.tryEmit(NativeEvent.LogLine("INFO", "Go", raw))
            }
        }
    }

    @Suppress("unused")
    fun onNativeCallEvent(uuid: String, json: String) {
        CallBridge.dispatchCallEvent(uuid, json)
    }

    // ---- Native declarations ----

    private external fun nativeInit(bridgeObj: Any): Boolean
    private external fun nativeGetVersion(): String?
    private external fun nativeStartNeonize(
        dbPath: String,
        uuid: String,
        jidBytes: ByteArray,
        newDevice: Int,
        logLevel: String,
        subscribers: ByteArray,
        devicePropsBytes: ByteArray,
        pairPhoneBytes: ByteArray,
    ): String?
    private external fun nativeStop(uuid: String)
    private external fun nativeStopAll()
    private external fun nativeDisconnect(uuid: String)
    private external fun nativeLogout(uuid: String): String?
    private external fun nativeIsConnected(uuid: String): Boolean
    private external fun nativeIsLoggedIn(uuid: String): Boolean
    private external fun nativePairPhone(uuid: String, pairPhoneParamsBytes: ByteArray): ByteArray?
    private external fun nativeGetAllDevices(dbPath: String): String?
    private external fun nativeGetMe(uuid: String): ByteArray?
    private external fun nativeSendPresence(uuid: String, presence: String): String?
    private external fun nativeSetPassive(uuid: String, passive: Boolean): String?
    private external fun nativeSendMessage(uuid: String, jidBytes: ByteArray, messageBytes: ByteArray): ByteArray?
    private external fun nativeMarkRead(
        uuid: String,
        idsSpaceJoined: String,
        timestampSec: Int,
        chatJidBytes: ByteArray,
        senderJidBytes: ByteArray,
        receiptType: String,
    ): String?
    private external fun nativeSendChatPresence(uuid: String, jidBytes: ByteArray, state: Int, media: Int): String?
    private external fun nativeGetAllContacts(uuid: String): ByteArray?
    private external fun nativeGetJoinedGroups(uuid: String): ByteArray?
    private external fun nativeDownloadAny(uuid: String, messageBytes: ByteArray): ByteArray?
    private external fun nativeSubscribePresence(uuid: String, jidBytes: ByteArray): String?
    private external fun nativePutPinned(uuid: String, jidBytes: ByteArray, pinned: Boolean): String?
    private external fun nativePutArchived(uuid: String, jidBytes: ByteArray, archived: Boolean): String?
    private external fun nativePutMutedUntil(uuid: String, jidBytes: ByteArray, untilSeconds: Float): String?
    private external fun nativeBuildReaction(
        uuid: String,
        chatJidBytes: ByteArray,
        senderJidBytes: ByteArray,
        messageId: String,
        reaction: String,
    ): ByteArray?
    private external fun nativeIsOnWhatsApp(uuid: String, numbersSpaceSeparated: String): ByteArray?
    private external fun nativeGetProfilePicture(uuid: String, jidBytes: ByteArray, paramsBytes: ByteArray): ByteArray?
    private external fun nativeUpload(uuid: String, mediaBytes: ByteArray, mediaType: Int): ByteArray?
    private external fun nativeCallPlace(uuid: String, target: String, isVideo: Int): String?
    private external fun nativeCallListActive(uuid: String): String?

    internal fun sanitizePhoneDigits(input: String): String =
        input.filter { it.isDigit() }

    internal fun parseAllDevicesString(raw: String): List<StoredDeviceInfo> {
        if (raw.isBlank() || raw.startsWith("error:")) return emptyList()
        return raw.split("|\u0001|")
            .mapNotNull { entry ->
                val parts = entry.split(",")
                if (parts.size < 4) return@mapNotNull null
                StoredDeviceInfo(
                    jid = parts[0],
                    pushName = parts[1],
                    businessName = parts[2],
                    initialized = parts[3].toBooleanStrictOrNull() ?: false,
                )
            }
    }

    val DEFAULT_SUBSCRIBED_EVENTS = byteArrayOf(
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
        13, 17, 18, 19, 20, 21, 22, 23, 26, 27, 52, 56, 58, 59, 68,
    )
}
