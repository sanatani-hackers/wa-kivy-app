package kt.sanatani.wapower.core

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kt.sanatani.wapower.jni.NativeEvent
import kt.sanatani.wapower.jni.NeonizeBridge
import kt.sanatani.wapower.jni.StoredDeviceInfo
import kt.sanatani.wapower.proto.NeonizeProtos
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class ConnectionPhase {
    STOPPED,
    CONNECTING,
    AWAITING_PAIRING,
    CONNECTED,
    READY,
    LOGGED_OUT,
    ERROR,
}

data class WaUiState(
    val phase: ConnectionPhase = ConnectionPhase.STOPPED,
    val syncState: SyncState = SyncState.STOPPED,
    val nativeVersion: String = "",
    val qrCode: String? = null,
    val pairingCode: String? = null,
    val pairingBusy: Boolean = false,
    val pairingError: String? = null,
    val selfJid: String? = null,
    val pushName: String? = null,
    val storedDevices: List<StoredDeviceInfo> = emptyList(),
    val chatCount: Int = 0,
    val messageCount: Int = 0,
    val contactCount: Int = 0,
    val ghostConfig: GhostConfig = GhostConfig(),
    val logs: List<String> = emptyList(),
    val lastError: String? = null,
)

object WaClientManager {
    const val SESSION_UUID = "wapower_main"
    private const val MAX_LOG_LINES = 80

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _state = MutableStateFlow(WaUiState())
    val state: StateFlow<WaUiState> = _state.asStateFlow()

    @Volatile
    private var appContext: Context? = null

    @Volatile
    private var _repository: WaRepository? = null
    val repository: WaRepository
        get() = checkNotNull(_repository) { "WaClientManager.init must be called first" }

    @Volatile
    private var neonizeJob: Job? = null

    @Volatile
    private var collectorStarted = false

    private val engineLock = Any()
    private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.US)

    fun init(context: Context) {
        synchronized(this) {
            if (appContext == null) {
                appContext = context.applicationContext
            }
            if (_repository == null) {
                _repository = WaRepository(
                    context = appContext!!,
                    sessionUuid = SESSION_UUID,
                    onLog = { msg -> appendLog(msg) },
                )
            }
        }
        startEventCollector()
        scope.launch {
            val ok = runCatching { NeonizeBridge.ensureInitialized() }.getOrElse { false }
            val ver = if (ok) runCatching { NeonizeBridge.getVersion() }.getOrDefault("") else ""
            val devices = if (ok) NeonizeBridge.getAllDevices(dbPath()) else emptyList()
            _state.update {
                it.copy(
                    nativeVersion = ver.ifBlank { "unavailable" },
                    storedDevices = devices,
                )
            }
            if (ok) {
                appendLog("JNI initialized (libneonize $ver, storedDevices=${devices.size})")
            } else {
                appendLog("ERROR: Failed to initialize native libneonize/wa_jni")
            }
        }
    }

    fun dbPath(): String {
        val ctx = checkNotNull(appContext) { "WaClientManager.init must be called first" }
        return File(ctx.filesDir, "session.sqlite3").absolutePath
    }

    fun startEngine(forceNewDevice: Boolean = false) {
        synchronized(engineLock) {
            if (neonizeJob?.isActive == true) {
                appendLog("Engine already running")
                return
            }
            startEventCollector()
            val path = dbPath()
            _repository?.onConnecting()
            _state.update {
                it.copy(
                    phase = ConnectionPhase.CONNECTING,
                    qrCode = null,
                    pairingCode = null,
                    pairingError = null,
                    lastError = null,
                )
            }
            appendLog("Starting Neonize session (db=$path, newDevice=$forceNewDevice)")

            neonizeJob = scope.launch {
                val devices = NeonizeBridge.getAllDevices(path)
                _state.update { it.copy(storedDevices = devices) }

                val err = NeonizeBridge.startSession(
                    dbPath = path,
                    uuid = SESSION_UUID,
                    jid = null,
                    newDevice = forceNewDevice,
                    logLevel = "INFO",
                )
                _repository?.onStopped()
                if (!err.isNullOrBlank()) {
                    appendLog("Neonize exited with error: $err")
                    _state.update {
                        it.copy(
                            phase = ConnectionPhase.ERROR,
                            lastError = err,
                            pairingBusy = false,
                        )
                    }
                } else {
                    appendLog("Neonize session stopped cleanly")
                    _state.update {
                        it.copy(
                            phase = ConnectionPhase.STOPPED,
                            pairingBusy = false,
                        )
                    }
                }
            }
        }
    }

    fun stopEngine() {
        appendLog("Stopping Neonize session...")
        scope.launch {
            NeonizeBridge.stop(SESSION_UUID)
            _repository?.onStopped()
            neonizeJob?.cancel()
            neonizeJob = null
            _state.update {
                it.copy(
                    phase = ConnectionPhase.STOPPED,
                    qrCode = null,
                    pairingCode = null,
                    pairingBusy = false,
                )
            }
        }
    }

    fun requestPairingCode(phoneInput: String) {
        val digits = NeonizeBridge.sanitizePhoneDigits(phoneInput)
        if (digits.length < 7) {
            _state.update {
                it.copy(pairingError = "Enter country code + phone number (at least 7 digits)")
            }
            return
        }

        scope.launch {
            _state.update {
                it.copy(
                    pairingBusy = true,
                    pairingError = null,
                    pairingCode = null,
                )
            }

            startEngine(forceNewDevice = false)

            var connectedReady = false
            val deadlineMs = System.currentTimeMillis() + 8000L
            while (isActive && System.currentTimeMillis() < deadlineMs) {
                if (NeonizeBridge.isConnected(SESSION_UUID) || _state.value.qrCode != null) {
                    connectedReady = true
                    break
                }
                delay(200L)
            }

            if (!connectedReady) {
                appendLog("Pairing wait timed out before WebSocket connected; attempting PairPhone anyway")
            }

            val result = NeonizeBridge.pairPhone(SESSION_UUID, digits)
            result.onSuccess { code ->
                appendLog("Pair code generated: $code")
                _state.update {
                    it.copy(
                        phase = ConnectionPhase.AWAITING_PAIRING,
                        pairingCode = code,
                        pairingBusy = false,
                        pairingError = null,
                    )
                }
            }.onFailure { ex ->
                val msg = ex.message ?: "Pairing code request failed"
                appendLog("PairPhone error: $msg")
                _state.update {
                    it.copy(
                        pairingBusy = false,
                        pairingError = msg,
                    )
                }
            }
        }
    }

    fun logoutAndReset() {
        scope.launch {
            appendLog("Logging out session...")
            val err = NeonizeBridge.logout(SESSION_UUID)
            if (!err.isNullOrBlank()) {
                appendLog("Logout note: $err")
            }
            NeonizeBridge.stop(SESSION_UUID)
            _repository?.onStopped()
            neonizeJob?.cancel()
            neonizeJob = null
            runCatching { File(dbPath()).delete() }
            _state.update {
                WaUiState(
                    nativeVersion = it.nativeVersion,
                    phase = ConnectionPhase.LOGGED_OUT,
                    ghostConfig = it.ghostConfig,
                    chatCount = it.chatCount,
                    messageCount = it.messageCount,
                    contactCount = it.contactCount,
                    logs = it.logs,
                )
            }
            appendLog("Session cleared")
        }
    }

    fun updateGhost(transform: (GhostConfig) -> GhostConfig) {
        _repository?.updateGhost(transform)
    }

    fun resyncContacts() {
        scope.launch {
            _repository?.resyncContacts()
        }
    }

    private fun startEventCollector() {
        if (collectorStarted) return
        collectorStarted = true

        _repository?.let { repo ->
            scope.launch {
                combine(
                    repo.syncState,
                    repo.ghostConfig,
                    repo.chatCount,
                    repo.messageCount,
                    repo.contactCount,
                ) { sync, ghost, chats, msgs, contacts ->
                    Triple(sync to ghost, chats to msgs, contacts)
                }.collect { (syncGhost, chatMsg, contacts) ->
                    val (sync, ghost) = syncGhost
                    val (chats, msgs) = chatMsg
                    _state.update { cur ->
                        val nextPhase = if (sync == SyncState.READY && cur.phase == ConnectionPhase.CONNECTED) {
                            ConnectionPhase.READY
                        } else {
                            cur.phase
                        }
                        cur.copy(
                            phase = nextPhase,
                            syncState = sync,
                            ghostConfig = ghost,
                            chatCount = chats,
                            messageCount = msgs,
                            contactCount = contacts,
                        )
                    }
                }
            }
        }

        scope.launch {
            NeonizeBridge.nativeEvents.collect { evt ->
                when (evt) {
                    is NativeEvent.QrCode -> {
                        appendLog("QR code received (${evt.code.length} chars)")
                        _state.update {
                            it.copy(
                                phase = ConnectionPhase.AWAITING_PAIRING,
                                qrCode = evt.code,
                            )
                        }
                    }

                    is NativeEvent.LoginStatus -> {
                        appendLog("Login status: ${evt.status}")
                        if (evt.status == "success") {
                            _state.update {
                                it.copy(
                                    phase = ConnectionPhase.CONNECTED,
                                    qrCode = null,
                                    pairingCode = null,
                                )
                            }
                        }
                    }

                    is NativeEvent.LogLine -> {
                        if (evt.level.equals("ERROR", ignoreCase = true) ||
                            evt.level.equals("WARN", ignoreCase = true)
                        ) {
                            appendLog("[${evt.name}/${evt.level}] ${evt.message}")
                        }
                    }

                    is NativeEvent.ProtoEvent -> {
                        handleUiProtoEvent(evt.code, evt.payload)
                        _repository?.handleProtoEvent(evt.code, evt.payload)
                    }
                }
            }
        }
    }

    private fun handleUiProtoEvent(code: Int, payload: ByteArray) {
        when (code) {
            0 -> { // Device
                val dev = runCatching { NeonizeProtos.Device.parseFrom(payload) }.getOrNull() ?: return
                val jid = if (dev.hasJID()) "${dev.jid.user}@${dev.jid.server}" else null
                appendLog("Device info: jid=$jid push=${dev.pushName}")
                _state.update {
                    it.copy(
                        selfJid = jid ?: it.selfJid,
                        pushName = dev.pushName.ifBlank { it.pushName },
                    )
                }
            }

            2 -> { // PairStatus
                val ps = runCatching { NeonizeProtos.PairStatus.parseFrom(payload) }.getOrNull() ?: return
                val jid = "${ps.id.user}@${ps.id.server}"
                appendLog("PairStatus: ${ps.status} ($jid)")
                if (ps.status == NeonizeProtos.PairStatus.PStatus.SUCCESS) {
                    val devices = NeonizeBridge.getAllDevices(dbPath())
                    _state.update {
                        it.copy(
                            phase = ConnectionPhase.CONNECTED,
                            selfJid = jid,
                            qrCode = null,
                            pairingCode = null,
                            storedDevices = devices,
                        )
                    }
                }
            }

            3 -> { // Connected
                val me = NeonizeBridge.getMe(SESSION_UUID)
                val devices = NeonizeBridge.getAllDevices(dbPath())
                val jid = me?.let { if (it.hasJID()) "${it.jid.user}@${it.jid.server}" else null }
                appendLog("Connected to WhatsApp Web (jid=${jid ?: "pending"})")
                _state.update {
                    it.copy(
                        phase = ConnectionPhase.CONNECTED,
                        qrCode = null,
                        pairingCode = null,
                        selfJid = jid ?: it.selfJid,
                        pushName = me?.pushName?.ifBlank { it.pushName } ?: it.pushName,
                        storedDevices = devices,
                        lastError = null,
                    )
                }
            }

            6 -> { // LoggedOut
                appendLog("WhatsApp reported LoggedOut")
                _state.update {
                    it.copy(phase = ConnectionPhase.LOGGED_OUT, qrCode = null, pairingCode = null)
                }
            }

            9 -> { // ConnectFailure
                val cf = runCatching { NeonizeProtos.ConnectFailure.parseFrom(payload) }.getOrNull()
                val msg = cf?.message ?: "Connection failed"
                appendLog("ConnectFailure: $msg")
                _state.update { it.copy(phase = ConnectionPhase.ERROR, lastError = msg) }
            }

            11 -> { // StreamError
                val se = runCatching { NeonizeProtos.StreamError.parseFrom(payload) }.getOrNull()
                appendLog("StreamError: code=${se?.code ?: "?"}")
            }

            12 -> { // Disconnected
                appendLog("Disconnected from WhatsApp")
                _state.update { it.copy(phase = ConnectionPhase.CONNECTING) }
            }

            27 -> { // OfflineSyncCompleted
                val osc = runCatching { NeonizeProtos.OfflineSyncCompleted.parseFrom(payload) }.getOrNull()
                appendLog("OfflineSyncCompleted (count=${osc?.count ?: 0})")
            }
        }
    }

    private fun appendLog(line: String) {
        val ts = timeFmt.format(Date())
        val entry = "$ts  $line"
        _state.update { cur ->
            val updated = (cur.logs + entry).takeLast(MAX_LOG_LINES)
            cur.copy(logs = updated)
        }
    }
}
