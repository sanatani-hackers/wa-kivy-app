package kt.sanatani.wapower.core

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SyncStateMachineTest {

    @Test
    fun offlineSyncCompleted_transitionsToReadyImmediately() = runTest {
        var readyCallbacks = 0
        val sm = SyncStateMachine(
            scope = backgroundScope,
            fallbackDelayMs = 3500L,
            onReadyCallback = { readyCallbacks++ },
        )

        sm.onConnecting()
        assertEquals(SyncState.CONNECTING, sm.state.value)

        sm.onWsConnected()
        assertEquals(SyncState.WS_CONNECTED, sm.state.value)

        sm.onOfflineSyncPreview()
        assertEquals(SyncState.SYNCING_OFFLINE, sm.state.value)

        sm.onOfflineSyncCompleted()
        runCurrent()
        assertEquals(SyncState.READY, sm.state.value)
        assertEquals(1, readyCallbacks)
        assertTrue(sm.awaitReady(100L))
    }

    @Test
    fun fallbackTimer_promotesWsConnectedToReadyAfter3500ms() = runTest {
        var readyCallbacks = 0
        val sm = SyncStateMachine(
            scope = backgroundScope,
            fallbackDelayMs = 3500L,
            onReadyCallback = { readyCallbacks++ },
        )

        sm.onConnecting()
        sm.onWsConnected()
        advanceTimeBy(3000L)
        runCurrent()
        assertEquals(SyncState.WS_CONNECTED, sm.state.value)
        assertEquals(0, readyCallbacks)

        advanceTimeBy(600L)
        runCurrent()
        assertEquals(SyncState.READY, sm.state.value)
        assertEquals(1, readyCallbacks)
    }

    @Test
    fun stoppedState_blocksReadyAndAwaitReturnsFalse() = runTest {
        val sm = SyncStateMachine(scope = backgroundScope, fallbackDelayMs = 3500L)
        sm.onStopped()
        assertFalse(sm.awaitReady(100L))
    }
}
