package kt.sanatani.wapower.calls

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kt.sanatani.wapower.MainActivity
import kt.sanatani.wapower.R
import kt.sanatani.wapower.WaMediaTranscoder
import kt.sanatani.wapower.db.CallLogEntity
import kt.sanatani.wapower.db.WaDatabase
import kt.sanatani.wapower.jni.CallBridge
import kt.sanatani.wapower.jni.CallNativeEvent
import kt.sanatani.wapower.jni.NeonizeBridge
import kt.sanatani.wapower.service.WaForegroundService
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

enum class BufferProfile(val label: String, val frames: Int) {
    FAST("Fast (4)", 4),
    NORMAL("Normal (8)", 8),
    SLOW("Safe (14)", 14),
}

enum class VideoQuality(val label: String, val maxHeight: Int) {
    Q360P("360p", 360),
    Q480P("480p", 480),
}

data class ActiveCallInfo(
    val callId: String,
    val peer: String,
    val peerDisplay: String,
    val direction: String,
    val isVideo: Boolean,
    val phase: String,
    val audioState: String = "idle",
    val nowPlaying: String = "",
    val startedAtSec: Long = System.currentTimeMillis() / 1000L,
)

data class QueuedTrack(
    val uid: String = UUID.randomUUID().toString().take(8),
    val queryOrUrl: String,
    val title: String,
    val asVideo: Boolean,
)

data class CallDeckState(
    val activeCalls: List<ActiveCallInfo> = emptyList(),
    val selectedCallId: String? = null,
    val queue: List<QueuedTrack> = emptyList(),
    val searchResults: List<YtSearchHit> = emptyList(),
    val searching: Boolean = false,
    val streamingBusy: Boolean = false,
    val bufferProfile: BufferProfile = BufferProfile.NORMAL,
    val videoQuality: VideoQuality = VideoQuality.Q480P,
    val loopTrack: Boolean = false,
    val statusNote: String = "",
)

class CallController(
    context: Context,
    private val sessionUuid: String,
    private val db: WaDatabase,
    private val onLog: (String) -> Unit,
) {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val yt = YouTubeInnerTube()
    private val activeStreams = ConcurrentHashMap<String, Job>()

    private val _deckState = MutableStateFlow(CallDeckState())
    val deckState: StateFlow<CallDeckState> = _deckState.asStateFlow()

    val callHistory: Flow<List<CallLogEntity>> = db.callLogDao().observeCallLogs()

    init {
        ensureCallNotificationChannel()
        scope.launch {
            CallBridge.events.collect { evt ->
                handleGoCallEvent(evt)
            }
        }
        scope.launch {
            CallBridge.logs.collect { line ->
                onLog("[VoIP] $line")
            }
        }
    }

    fun reconcileActiveCalls() {
        scope.launch {
            val raw = runCatching { NeonizeBridge.listActiveCallsJson(sessionUuid) }.getOrNull() ?: return@launch
            val obj = runCatching { JSONObject(raw) }.getOrNull() ?: return@launch
            if (!obj.optBoolean("ok", true)) return@launch
            val arr = obj.optJSONArray("calls") ?: return@launch
            val nowSec = System.currentTimeMillis() / 1000L
            for (i in 0 until arr.length()) {
                val c = arr.optJSONObject(i) ?: continue
                val cid = c.optString("call_id", "").trim()
                if (cid.isBlank()) continue
                val peer = c.optString("peer", "")
                val direction = c.optString("direction", "in")
                val phase = c.optString("phase", "ringing")
                if (phase == "ended") continue
                val existing = _deckState.value.activeCalls.firstOrNull { it.callId == cid }
                val info = ActiveCallInfo(
                    callId = cid,
                    peer = peer,
                    peerDisplay = peer.substringBefore('@').substringBefore(':'),
                    direction = direction,
                    isVideo = c.optBoolean("is_video", false),
                    phase = phase,
                    audioState = c.optString("audio_state", existing?.audioState ?: "idle"),
                    nowPlaying = existing?.nowPlaying ?: c.optString("audio_path", ""),
                    startedAtSec = existing?.startedAtSec ?: nowSec,
                )
                addOrUpdateCall(info)
                if (existing == null && direction == "in" && phase !in setOf("active", "ended")) {
                    showIncomingCallNotification(info)
                }
            }
        }
    }

    fun setBufferProfile(profile: BufferProfile) {
        _deckState.update { it.copy(bufferProfile = profile) }
    }

    fun setVideoQuality(quality: VideoQuality) {
        _deckState.update { it.copy(videoQuality = quality) }
    }

    fun setLoopTrack(loop: Boolean) {
        _deckState.update { it.copy(loopTrack = loop) }
    }

    fun selectCall(callId: String) {
        _deckState.update { it.copy(selectedCallId = callId) }
    }

    fun searchYouTube(query: String) {
        val q = query.trim()
        if (q.isEmpty()) return
        scope.launch {
            _deckState.update { it.copy(searching = true, statusNote = "") }
            runCatching { yt.search(q, limit = 6) }
                .onSuccess { hits ->
                    _deckState.update {
                        it.copy(
                            searching = false,
                            searchResults = hits,
                            statusNote = if (hits.isEmpty()) "No YouTube results" else "",
                        )
                    }
                }
                .onFailure { err ->
                    _deckState.update {
                        it.copy(searching = false, statusNote = err.message ?: "Search failed")
                    }
                }
        }
    }

    fun enqueueTrack(queryOrUrl: String, title: String, asVideo: Boolean) {
        val item = QueuedTrack(
            queryOrUrl = queryOrUrl,
            title = title.ifBlank { queryOrUrl },
            asVideo = asVideo,
        )
        _deckState.update { it.copy(queue = it.queue + item, statusNote = "Queued: ${item.title}") }
    }

    fun removeQueuedTrack(uid: String) {
        _deckState.update { it.copy(queue = it.queue.filterNot { q -> q.uid == uid }) }
    }

    fun placeCall(target: String, isVideo: Boolean, autoPlayQuery: String? = null, autoPlayVideo: Boolean = false) {
        val tgt = target.trim()
        if (tgt.isEmpty()) {
            _deckState.update { it.copy(statusNote = "Enter target number or JID") }
            return
        }
        scope.launch {
            val raw = NeonizeBridge.callPlace(sessionUuid, tgt, isVideo)
            val obj = runCatching { JSONObject(raw) }.getOrNull()
            if (obj?.optBoolean("ok") == true) {
                val cid = obj.optString("call_id", "")
                val peer = obj.optString("peer", tgt)
                val nowSec = System.currentTimeMillis() / 1000L
                val info = ActiveCallInfo(
                    callId = cid,
                    peer = peer,
                    peerDisplay = peer.substringBefore('@').substringBefore(':'),
                    direction = "out",
                    isVideo = isVideo,
                    phase = obj.optString("phase", "calling"),
                    startedAtSec = nowSec,
                )
                addOrUpdateCall(info)
                db.callLogDao().upsert(
                    CallLogEntity(
                        callId = cid,
                        peerJid = peer,
                        direction = "out",
                        isVideo = isVideo,
                        status = "calling",
                        startedAt = nowSec,
                    )
                )
                onLog("Placed ${if (isVideo) "video" else "voice"} call to ${info.peerDisplay}")
                if (!autoPlayQuery.isNullOrBlank()) {
                    if (autoPlayVideo) {
                        streamYouTubeVideo(cid, autoPlayQuery)
                    } else {
                        streamYouTubeAudio(cid, autoPlayQuery)
                    }
                }
            } else {
                val err = obj?.optString("error", "CallPlace failed") ?: "CallPlace failed"
                _deckState.update { it.copy(statusNote = err) }
                onLog("CallPlace failed: $err")
            }
        }
    }

    fun answerCall(callId: String, withAutoMusicQuery: String? = null) {
        scope.launch {
            val raw = NeonizeBridge.callAnswer(sessionUuid, callId)
            val obj = runCatching { JSONObject(raw) }.getOrNull()
            dismissIncomingNotification()
            if (obj?.optBoolean("ok") == true) {
                onLog("Answered call $callId")
                if (!withAutoMusicQuery.isNullOrBlank()) {
                    delay(400L)
                    streamYouTubeAudio(callId, withAutoMusicQuery)
                }
            } else {
                _deckState.update { it.copy(statusNote = obj?.optString("error", "Answer failed") ?: "Answer failed") }
            }
        }
    }

    fun declineCall(callId: String) {
        scope.launch {
            dismissIncomingNotification()
            NeonizeBridge.callDecline(sessionUuid, callId)
        }
    }

    fun hangupCall(callId: String) {
        scope.launch {
            activeStreams.remove(callId)?.cancel()
            NeonizeBridge.callHangup(sessionUuid, callId)
        }
    }

    fun pauseAudio(callId: String) {
        scope.launch {
            NeonizeBridge.callPauseAudio(sessionUuid, callId)
            updateCall(callId) { it.copy(audioState = "paused") }
        }
    }

    fun resumeAudio(callId: String) {
        scope.launch {
            NeonizeBridge.callResumeAudio(sessionUuid, callId)
            updateCall(callId) { it.copy(audioState = "playing") }
        }
    }

    fun stopMedia(callId: String) {
        scope.launch {
            activeStreams.remove(callId)?.cancel()
            NeonizeBridge.callStopAudio(sessionUuid, callId)
            NeonizeBridge.callStopVideoStream(sessionUuid, callId)
            updateCall(callId) { it.copy(audioState = "idle", nowPlaying = "") }
        }
    }

    fun skipNext(callId: String) {
        val next = _deckState.value.queue.firstOrNull()
        if (next == null) {
            stopMedia(callId)
            return
        }
        _deckState.update { it.copy(queue = it.queue.drop(1)) }
        if (next.asVideo) {
            streamYouTubeVideo(callId, next.queryOrUrl)
        } else {
            streamYouTubeAudio(callId, next.queryOrUrl)
        }
    }

    fun streamYouTubeAudio(callId: String, queryOrUrl: String) {
        val q = queryOrUrl.trim()
        if (q.isEmpty()) return
        activeStreams.remove(callId)?.cancel()

        val job = scope.launch {
            _deckState.update { it.copy(streamingBusy = true, statusNote = "Resolving YouTube audio...") }
            try {
                for (attempt in 0..1) {
                    val resolved = yt.resolveAudioStream(q, maxKbps = 160, maxMinutes = 60)
                    val prebuf = _deckState.value.bufferProfile.frames
                    val resRaw = NeonizeBridge.callPlayPcmStream(sessionUuid, callId, resolved.title, prebuf)
                    val resObj = JSONObject(resRaw)
                    if (!resObj.optBoolean("ok")) {
                        throw IllegalStateException(resObj.optString("error", "CallPlayPCMStream failed"))
                    }
                    val port = resObj.optInt("port", 0)
                    if (port <= 0) throw IllegalStateException("Invalid PCM socket port")

                    WaMediaTranscoder.clearStreamState(port)
                    updateCall(callId) { it.copy(audioState = "playing", nowPlaying = resolved.title) }
                    _deckState.update {
                        it.copy(
                            streamingBusy = false,
                            statusNote = "Live audio: ${resolved.title} (${resolved.client})",
                        )
                    }

                    val err = withContext(Dispatchers.IO) {
                        WaMediaTranscoder.streamUrlToPcmSocket(
                            resolved.url,
                            resolved.headersJson,
                            resolved.contentLength,
                            "127.0.0.1",
                            port,
                            3600,
                        )
                    }
                    if (err.startsWith("HTTP_REFUSED:") && attempt == 0) {
                        yt.reportRefused(resolved.videoId, resolved.client)
                        NeonizeBridge.callStopAudio(sessionUuid, callId)
                        continue
                    }
                    if (err.isNotBlank()) {
                        _deckState.update { it.copy(statusNote = err) }
                        NeonizeBridge.callStopAudio(sessionUuid, callId)
                    }
                    break
                }
            } catch (e: Exception) {
                _deckState.update { it.copy(streamingBusy = false, statusNote = e.message ?: "Stream error") }
                onLog("YouTube audio stream error: ${e.message}")
            }
        }
        activeStreams[callId] = job
    }

    fun streamYouTubeVideo(callId: String, queryOrUrl: String) {
        val q = queryOrUrl.trim()
        if (q.isEmpty()) return
        activeStreams.remove(callId)?.cancel()

        val job = scope.launch {
            _deckState.update { it.copy(streamingBusy = true, statusNote = "Resolving YouTube video...") }
            try {
                for (attempt in 0..1) {
                    val quality = _deckState.value.videoQuality
                    val resolved = yt.resolveAvStream(q, maxHeight = quality.maxHeight, maxKbps = 160)
                    val prebuf = _deckState.value.bufferProfile.frames
                    val resRaw = NeonizeBridge.callPlayAvStream(
                        sessionUuid,
                        callId,
                        resolved.title,
                        resolved.fps,
                        prebuf,
                    )
                    val resObj = JSONObject(resRaw)
                    if (!resObj.optBoolean("ok")) {
                        throw IllegalStateException(resObj.optString("error", "CallPlayAVStream failed"))
                    }
                    val audioPort = resObj.optInt("audio_port", 0)
                    val videoPort = resObj.optInt("video_port", 0)
                    if (audioPort <= 0 || videoPort <= 0) throw IllegalStateException("Invalid AV loopback ports")

                    WaMediaTranscoder.clearStreamState(audioPort)
                    WaMediaTranscoder.clearStreamState(videoPort)
                    updateCall(callId) { it.copy(audioState = "playing", nowPlaying = "${resolved.title} (${resolved.qualityLabel})") }
                    _deckState.update {
                        it.copy(
                            streamingBusy = false,
                            statusNote = "Live ${resolved.qualityLabel}: ${resolved.title}",
                        )
                    }

                    val err = withContext(Dispatchers.IO) {
                        WaMediaTranscoder.streamAvUrlsToSockets(
                            resolved.videoUrl,
                            resolved.audioUrl,
                            resolved.headersJson,
                            resolved.videoContentLength,
                            resolved.audioContentLength,
                            videoPort,
                            audioPort,
                            quality.maxHeight,
                            resolved.fps,
                            650_000,
                            600,
                        )
                    }
                    if (err.startsWith("HTTP_REFUSED:") && attempt == 0) {
                        yt.reportRefused(resolved.videoId, resolved.client)
                        NeonizeBridge.callStopAudio(sessionUuid, callId)
                        NeonizeBridge.callStopVideoStream(sessionUuid, callId)
                        continue
                    }
                    if (err.isNotBlank()) {
                        _deckState.update { it.copy(statusNote = err) }
                    }
                    break
                }
            } catch (e: Exception) {
                _deckState.update { it.copy(streamingBusy = false, statusNote = e.message ?: "AV stream error") }
                onLog("YouTube AV stream error: ${e.message}")
            }
        }
        activeStreams[callId] = job
    }

    fun playLocalMediaFile(callId: String, uri: Uri, mimeType: String, displayName: String) {
        val targetCallId = callId.ifBlank { _deckState.value.activeCalls.firstOrNull()?.callId.orEmpty() }
        if (targetCallId.isBlank()) {
            _deckState.update { it.copy(statusNote = "No active call selected") }
            return
        }
        activeStreams.remove(targetCallId)?.cancel()
        scope.launch {
            val cleanName = displayName.ifBlank { "track" }
            _deckState.update { it.copy(streamingBusy = true, statusNote = "Preparing $cleanName...") }
            runCatching {
                val trackDir = File(appContext.filesDir, "call_tracks").apply { mkdirs() }
                val safeName = cleanName.filter { it.isLetterOrDigit() || it == '.' || it == '_' }
                    .ifBlank { "track.bin" }
                val rawFile = File(trackDir, "src_${UUID.randomUUID().toString().take(8)}_$safeName")
                appContext.contentResolver.openInputStream(uri)?.use { input ->
                    rawFile.outputStream().use { out -> input.copyTo(out) }
                } ?: error("Cannot open selected file")

                val wavFile = File(trackDir, "${rawFile.nameWithoutExtension}.wav")
                val audioErr = WaMediaTranscoder.extractAudioToWav16k(rawFile.absolutePath, wavFile.absolutePath)

                if (isVideoMedia(mimeType, cleanName)) {
                    val h264File = File(trackDir, "${rawFile.nameWithoutExtension}.h264")
                    val vidErr = WaMediaTranscoder.transcodeVideoToH264(
                        rawFile.absolutePath,
                        h264File.absolutePath,
                        _deckState.value.videoQuality.maxHeight,
                        15,
                        700_000,
                    )
                    if (vidErr.isNotBlank()) error(vidErr)
                    val resRaw = NeonizeBridge.callPlayAv(
                        sessionUuid,
                        targetCallId,
                        h264File.absolutePath,
                        if (audioErr.isBlank()) wavFile.absolutePath else "",
                        15,
                        _deckState.value.loopTrack,
                    )
                    val obj = JSONObject(resRaw)
                    if (!obj.optBoolean("ok")) error(obj.optString("error", "CallPlayAV failed"))
                } else {
                    if (audioErr.isNotBlank()) error(audioErr)
                    val resRaw = NeonizeBridge.callPlayAudio(
                        sessionUuid,
                        targetCallId,
                        wavFile.absolutePath,
                        _deckState.value.loopTrack,
                    )
                    val obj = JSONObject(resRaw)
                    if (!obj.optBoolean("ok")) error(obj.optString("error", "CallPlayAudio failed"))
                }
                updateCall(targetCallId) { it.copy(audioState = "playing", nowPlaying = cleanName) }
                _deckState.update { it.copy(streamingBusy = false, statusNote = "Playing local: $cleanName") }
            }.onFailure { err ->
                _deckState.update { it.copy(streamingBusy = false, statusNote = err.message ?: "Local play failed") }
                onLog("Local media failed: ${err.message}")
            }
        }
    }

    private suspend fun handleGoCallEvent(evt: CallNativeEvent) {
        if (evt.event == "log") return
        val obj = runCatching { JSONObject(evt.rawJson) }.getOrNull() ?: JSONObject()
        val cid = evt.callId
        val peer = evt.peer
        val direction = obj.optString("direction", "in")
        val isVideo = obj.optBoolean("is_video", false)
        val nowSec = obj.optLong("ts", System.currentTimeMillis() / 1000L)

        when (evt.event) {
            "incoming" -> {
                val info = ActiveCallInfo(
                    callId = cid,
                    peer = peer,
                    peerDisplay = peer.substringBefore('@').substringBefore(':'),
                    direction = "in",
                    isVideo = isVideo,
                    phase = evt.phase.ifBlank { "ringing" },
                    startedAtSec = nowSec,
                )
                addOrUpdateCall(info)
                db.callLogDao().upsert(
                    CallLogEntity(
                        callId = cid,
                        peerJid = peer,
                        direction = "in",
                        isVideo = isVideo,
                        status = "ringing",
                        startedAt = nowSec,
                    )
                )
                showIncomingCallNotification(info)
                onLog("Incoming ${if (isVideo) "video" else "voice"} call from ${info.peerDisplay}")
            }

            "ready", "peer_accept" -> {
                dismissIncomingNotification()
                updateCall(cid) { it.copy(phase = "active") }
                db.callLogDao().upsert(
                    CallLogEntity(
                        callId = cid,
                        peerJid = peer,
                        direction = direction,
                        isVideo = isVideo,
                        status = "active",
                        startedAt = nowSec,
                    )
                )
            }

            "state" -> {
                if (evt.phase.isNotBlank()) {
                    updateCall(cid) { it.copy(phase = evt.phase) }
                }
            }

            "audio_finish" -> {
                updateCall(cid) { it.copy(audioState = "idle") }
                if (_deckState.value.queue.isNotEmpty()) {
                    skipNext(cid)
                }
            }

            "audio_error", "video_error" -> {
                val err = obj.optString("error", "Media stream error")
                _deckState.update { it.copy(statusNote = err) }
                onLog("Call media error ($cid): $err")
            }

            "end" -> {
                dismissIncomingNotification()
                activeStreams.remove(cid)?.cancel()
                val existing = _deckState.value.activeCalls.firstOrNull { it.callId == cid }
                val started = existing?.startedAtSec ?: nowSec
                val reason = obj.optString("reason", "ended")
                val status = when (reason) {
                    "rejected", "declined" -> "rejected"
                    "timeout", "missed" -> "missed"
                    else -> "ended"
                }
                db.callLogDao().upsert(
                    CallLogEntity(
                        callId = cid,
                        peerJid = peer.ifBlank { existing?.peer.orEmpty() },
                        direction = existing?.direction ?: direction,
                        isVideo = existing?.isVideo ?: isVideo,
                        status = status,
                        startedAt = started,
                        endedAt = nowSec,
                        durationSec = (nowSec - started).coerceAtLeast(0L),
                    )
                )
                _deckState.update { cur ->
                    val remaining = cur.activeCalls.filterNot { it.callId == cid }
                    cur.copy(
                        activeCalls = remaining,
                        selectedCallId = if (cur.selectedCallId == cid) remaining.firstOrNull()?.callId else cur.selectedCallId,
                    )
                }
                onLog("Call ended ($cid): $reason")
            }
        }
    }

    private fun addOrUpdateCall(info: ActiveCallInfo) {
        _deckState.update { cur ->
            val exists = cur.activeCalls.any { it.callId == info.callId }
            val list = if (exists) {
                cur.activeCalls.map { if (it.callId == info.callId) info else it }
            } else {
                listOf(info) + cur.activeCalls
            }
            cur.copy(
                activeCalls = list,
                selectedCallId = cur.selectedCallId ?: info.callId,
            )
        }
    }

    private fun updateCall(callId: String, transform: (ActiveCallInfo) -> ActiveCallInfo) {
        _deckState.update { cur ->
            cur.copy(
                activeCalls = cur.activeCalls.map {
                    if (it.callId == callId) transform(it) else it
                }
            )
        }
    }

    private fun ensureCallNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(
                CALL_CHANNEL_ID,
                "Incoming WhatsApp Calls",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Incoming VoIP call alerts with Answer, Answer + Music, and Decline"
            }
            appContext.getSystemService(NotificationManager::class.java)?.createNotificationChannel(ch)
        }
    }

    private fun showIncomingCallNotification(call: ActiveCallInfo) {
        val openIntent = PendingIntent.getActivity(
            appContext,
            20,
            Intent(appContext, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val answerIntent = PendingIntent.getService(
            appContext,
            21,
            Intent(appContext, WaForegroundService::class.java).apply {
                action = WaForegroundService.ACTION_CALL_ANSWER
                putExtra(WaForegroundService.EXTRA_CALL_ID, call.callId)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val answerMusicIntent = PendingIntent.getService(
            appContext,
            22,
            Intent(appContext, WaForegroundService::class.java).apply {
                action = WaForegroundService.ACTION_CALL_ANSWER_MUSIC
                putExtra(WaForegroundService.EXTRA_CALL_ID, call.callId)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val declineIntent = PendingIntent.getService(
            appContext,
            23,
            Intent(appContext, WaForegroundService::class.java).apply {
                action = WaForegroundService.ACTION_CALL_DECLINE
                putExtra(WaForegroundService.EXTRA_CALL_ID, call.callId)
            },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val notif: Notification = NotificationCompat.Builder(appContext, CALL_CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(if (call.isVideo) "Incoming video call" else "Incoming voice call")
            .setContentText(call.peerDisplay.ifBlank { call.peer })
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setDefaults(Notification.DEFAULT_ALL)
            .setFullScreenIntent(openIntent, true)
            .setOngoing(true)
            .setContentIntent(openIntent)
            .addAction(0, "Answer", answerIntent)
            .addAction(0, "Answer + Music", answerMusicIntent)
            .addAction(0, "Decline", declineIntent)
            .build()

        runCatching {
            appContext.getSystemService(NotificationManager::class.java)?.notify(CALL_NOTIF_ID, notif)
        }
    }

    private fun dismissIncomingNotification() {
        runCatching {
            appContext.getSystemService(NotificationManager::class.java)?.cancel(CALL_NOTIF_ID)
        }
    }

    companion object {
        private const val CALL_CHANNEL_ID = "wa_power_calls_v2"
        private const val CALL_NOTIF_ID = 2002
        private val VIDEO_EXTS = setOf("mp4", "mkv", "mov", "webm", "3gp", "avi", "m4v")

        internal fun isVideoMedia(mimeType: String, displayName: String): Boolean {
            if (mimeType.trim().lowercase().startsWith("video/")) return true
            val ext = displayName.substringAfterLast('.', "").trim().lowercase()
            return ext in VIDEO_EXTS
        }
    }
}
