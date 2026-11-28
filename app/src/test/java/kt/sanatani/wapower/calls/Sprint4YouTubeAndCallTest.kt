package kt.sanatani.wapower.calls

import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import kt.sanatani.wapower.jni.CallBridge
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class Sprint4YouTubeAndCallTest {

    private fun stream(
        itag: Int,
        mimeType: String,
        bitrate: Int,
        hasAudio: Boolean,
        hasVideo: Boolean,
        height: Int = 0,
        client: String = "ANDROID_VR",
    ) = YtStream(
        itag = itag,
        url = "https://example.com/$itag",
        mimeType = mimeType,
        codecs = "",
        bitrate = bitrate,
        contentLength = 1024L,
        hasAudio = hasAudio,
        hasVideo = hasVideo,
        client = client,
        headers = emptyMap(),
        width = if (height == 480) 854 else if (height == 360) 640 else 0,
        height = height,
        fps = if (hasVideo) 30 else 0,
    )

    @Test
    fun extractVideoIdOrNull_parsesUrlsAndRawIds() {
        assertEquals("dQw4w9WgXcQ", YouTubeInnerTube.extractVideoIdOrNull("dQw4w9WgXcQ"))
        assertEquals("dQw4w9WgXcQ", YouTubeInnerTube.extractVideoIdOrNull("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        assertEquals("dQw4w9WgXcQ", YouTubeInnerTube.extractVideoIdOrNull("https://youtu.be/dQw4w9WgXcQ?t=42"))
        assertEquals("dQw4w9WgXcQ", YouTubeInnerTube.extractVideoIdOrNull("https://youtube.com/shorts/dQw4w9WgXcQ"))
        assertEquals("dQw4w9WgXcQ", YouTubeInnerTube.extractVideoIdOrNull("https://music.youtube.com/live/dQw4w9WgXcQ"))
        assertNull(YouTubeInnerTube.extractVideoIdOrNull("not a video id"))
        assertNull(YouTubeInnerTube.extractVideoIdOrNull(""))
    }

    @Test
    fun parseDurationSec_handlesMmSsAndHhMmSs() {
        assertEquals(0, YouTubeInnerTube.parseDurationSec(""))
        assertEquals(0, YouTubeInnerTube.parseDurationSec("LIVE"))
        assertEquals(225, YouTubeInnerTube.parseDurationSec("3:45"))
        assertEquals(3661, YouTubeInnerTube.parseDurationSec("1:01:01"))
    }

    @Test
    fun selectAudioStream_prefersM4aUnderMaxKbps() {
        val m4aLow = stream(139, "audio/mp4", 48_000, hasAudio = true, hasVideo = false)
        val m4a140 = stream(140, "audio/mp4", 128_000, hasAudio = true, hasVideo = false)
        val webmHigh = stream(251, "audio/webm", 150_000, hasAudio = true, hasVideo = false)
        val m4aTooHigh = stream(141, "audio/mp4", 256_000, hasAudio = true, hasVideo = false)

        val picked = YouTubeInnerTube.selectAudioStream(
            streams = listOf(m4aLow, webmHigh, m4a140, m4aTooHigh),
            maxKbps = 160,
        )
        assertNotNull(picked)
        assertEquals(140, picked?.itag)
    }

    @Test
    fun selectAvPair_prefersAdaptive480Plus140OverMuxed18() {
        val v360Adaptive = stream(134, "video/mp4", 600_000, hasAudio = false, hasVideo = true, height = 360)
        val v480Adaptive = stream(135, "video/mp4", 900_000, hasAudio = false, hasVideo = true, height = 480)
        val a140 = stream(140, "audio/mp4", 128_000, hasAudio = true, hasVideo = false)
        val muxed18 = stream(18, "video/mp4", 500_000, hasAudio = true, hasVideo = true, height = 360)

        val (video480, audio480) = YouTubeInnerTube.selectAvPair(
            streams = listOf(muxed18, v360Adaptive, v480Adaptive, a140),
            maxHeight = 480,
            maxKbps = 160,
        )
        assertEquals(135, video480.itag)
        assertEquals(140, audio480?.itag)

        val (video360, audio360) = YouTubeInnerTube.selectAvPair(
            streams = listOf(muxed18, v360Adaptive, v480Adaptive, a140),
            maxHeight = 360,
            maxKbps = 160,
        )
        assertEquals(134, video360.itag)
        assertEquals(140, audio360?.itag)
    }

    @Test
    fun selectAvPair_fallsBackToMuxed18WhenAdaptiveMissing() {
        val muxed18 = stream(18, "video/mp4", 500_000, hasAudio = true, hasVideo = true, height = 360)
        val (video, audio) = YouTubeInnerTube.selectAvPair(listOf(muxed18), maxHeight = 480, maxKbps = 160)
        assertEquals(18, video.itag)
        assertNull(audio)
    }

    @Test
    fun callStreamingProfiles_matchExpectedConstants() {
        assertEquals(4, BufferProfile.FAST.frames)
        assertEquals(8, BufferProfile.NORMAL.frames)
        assertEquals(14, BufferProfile.SLOW.frames)
        assertEquals(360, VideoQuality.Q360P.maxHeight)
        assertEquals(480, VideoQuality.Q480P.maxHeight)
    }

    @Test
    fun isVideoMedia_detectsMimeAndExtensionFallback() {
        assertTrue(CallController.isVideoMedia("video/mp4", "clip.bin"))
        assertTrue(CallController.isVideoMedia("application/octet-stream", "clip.mp4"))
        assertTrue(CallController.isVideoMedia("", "clip.MKV"))
        assertFalse(CallController.isVideoMedia("audio/mpeg", "song.mp3"))
        assertFalse(CallController.isVideoMedia("application/octet-stream", "voice.wav"))
    }

    @Test
    fun callBridge_separatesLogsFromCallEvents() = runTest {
        val nextEvent = async { CallBridge.events.first() }
        testScheduler.advanceUntilIdle()
        repeat(100) { idx ->
            CallBridge.dispatchCallEvent("u1", """{"event":"log","msg":"noise $idx"}""")
        }
        CallBridge.dispatchCallEvent(
            "u1",
            """{"event":"incoming","call_id":"C123","peer":"91999@s.whatsapp.net","phase":"ringing"}""",
        )
        val evt = nextEvent.await()
        assertEquals("incoming", evt.event)
        assertEquals("C123", evt.callId)
        assertEquals("ringing", evt.phase)
    }
}
