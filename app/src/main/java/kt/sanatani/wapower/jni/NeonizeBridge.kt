package kt.sanatani.wapower.jni

import kt.sanatani.wapower.proto.NeonizeProtos
import kt.sanatani.wapower.proto.waCompanionReg.WACompanionReg.DeviceProps
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
