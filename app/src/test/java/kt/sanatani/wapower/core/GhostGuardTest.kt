package kt.sanatani.wapower.core

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GhostGuardTest {

    @Test
    fun defaults_allowAllWireFrames() {
        val cfg = GhostConfig()
        val guard = GhostGuard { cfg }
        assertTrue(guard.allowMarkRead())
        assertTrue(guard.allowPresence())
        assertTrue(guard.allowTyping())
        assertTrue(guard.allowViewOnceReceipt())
    }

    @Test
    fun enabledFlags_suppressMatchingWireFrames() {
        var cfg = GhostConfig(
            ghostRead = true,
            ghostPresence = true,
            ghostTyping = true,
            ghostViewOnceInline = true,
        )
        val guard = GhostGuard { cfg }
        assertFalse(guard.allowMarkRead())
        assertFalse(guard.allowPresence())
        assertFalse(guard.allowTyping())
        assertFalse(guard.allowViewOnceReceipt())

        cfg = cfg.copy(ghostRead = false, ghostTyping = false)
        assertTrue(guard.allowMarkRead())
        assertFalse(guard.allowPresence())
        assertTrue(guard.allowTyping())
    }
}
