package kt.sanatani.wapower.core

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

enum class SyncState {
    STOPPED,
    CONNECTING,
    WS_CONNECTED,
    SYNCING_OFFLINE,
    READY,
}

class SyncStateMachine(
    private val scope: CoroutineScope,
    private val fallbackDelayMs: Long = 3500L,
    private val onReadyCallback: (suspend () -> Unit)? = null,
) {
    private val _state = MutableStateFlow(SyncState.STOPPED)
    val state: StateFlow<SyncState> = _state.asStateFlow()

    private val lock = Any()
    private var fallbackJob: Job? = null

    fun onStopped() = transition(SyncState.STOPPED)

    fun onConnecting() = transition(SyncState.CONNECTING)

    fun onWsConnected() {
        val entered = transition(SyncState.WS_CONNECTED)
        if (entered) {
            scheduleFallbackReady()
        }
    }

    fun onOfflineSyncPreview() {
        transition(SyncState.SYNCING_OFFLINE)
    }

    fun onOfflineSyncCompleted() {
        markReady()
    }

    fun onAppStateSyncComplete() {
        if (_state.value != SyncState.READY && _state.value != SyncState.STOPPED) {
            markReady()
        }
    }

    suspend fun awaitReady(timeoutMs: Long = 6000L): Boolean {
        val cur = _state.value
        if (cur == SyncState.READY) return true
        if (cur == SyncState.STOPPED) return false
        val reached = withTimeoutOrNull(timeoutMs) {
            _state.first { it == SyncState.READY || it == SyncState.STOPPED }
        }
        return reached == SyncState.READY
    }

    private fun markReady() {
        val changed = transition(SyncState.READY)
        if (changed && onReadyCallback != null) {
            scope.launch { onReadyCallback.invoke() }
        }
    }

    private fun scheduleFallbackReady() {
        synchronized(lock) {
            fallbackJob?.cancel()
            fallbackJob = scope.launch {
                delay(fallbackDelayMs)
                val cur = _state.value
                if (cur == SyncState.WS_CONNECTED || cur == SyncState.SYNCING_OFFLINE) {
                    markReady()
                }
            }
        }
    }

    private fun transition(target: SyncState): Boolean {
        synchronized(lock) {
            val cur = _state.value
            if ((target == SyncState.WS_CONNECTED || target == SyncState.SYNCING_OFFLINE) && cur == SyncState.READY) {
                return false
            }
            if (target == SyncState.STOPPED || target == SyncState.CONNECTING || target == SyncState.READY) {
                fallbackJob?.cancel()
                fallbackJob = null
            }
            if (cur == target) return false
            _state.value = target
            return true
        }
    }
}
