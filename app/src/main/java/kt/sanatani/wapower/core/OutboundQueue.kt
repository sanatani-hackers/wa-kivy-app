package kt.sanatani.wapower.core

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.random.Random

data class OutboundJob(
    val chatJid: String,
    val text: String,
    val localMsgId: String,
    val quotedId: String = "",
    val source: String = "ui",
)

class OutboundQueue(
    private val scope: CoroutineScope,
    private val awaitReady: suspend () -> Boolean,
    private val sendTyping: suspend (String, Boolean) -> Unit,
    private val sendActual: suspend (OutboundJob) -> Unit,
    private val minDelayMs: Long = 300L,
    private val maxDelayMs: Long = 900L,
    private val perChatGapMs: Long = 1500L,
    private val maxPerMin: Int = 30,
) {
    private val channel = Channel<OutboundJob>(capacity = 100)
    private val sentWindow = ArrayDeque<Long>()
    private val lastChatSend = HashMap<String, Long>()
    private var started = false

    @Synchronized
    fun start() {
        if (started) return
        started = true
        scope.launch(Dispatchers.IO) {
            for (job in channel) {
                runCatching {
                    if (!awaitReady()) return@runCatching
                    pace(job)
                    sendActual(job)
                }
            }
        }
    }

    fun enqueue(job: OutboundJob): Boolean {
        start()
        return channel.trySend(job).isSuccess
    }

    private suspend fun pace(job: OutboundJob) {
        val now = System.currentTimeMillis()
        while (sentWindow.isNotEmpty() && now - sentWindow.first() > 60_000L) {
            sentWindow.removeFirst()
        }
        if (sentWindow.size >= maxPerMin) {
            val waitMs = 60_000L - (now - sentWindow.first()) + Random.nextLong(500L, 1500L)
            if (waitMs > 0) delay(waitMs)
        }

        val gap = (lastChatSend[job.chatJid] ?: 0L) + perChatGapMs - System.currentTimeMillis()
        if (gap > 0) delay(gap)

        runCatching { sendTyping(job.chatJid, true) }
        val base = Random.nextLong(minDelayMs, maxDelayMs + 1)
        val typingExtra = (job.text.length * 15L).coerceAtMost(2000L)
        delay(base + typingExtra)
        runCatching { sendTyping(job.chatJid, false) }

        val stamp = System.currentTimeMillis()
        sentWindow.addLast(stamp)
        lastChatSend[job.chatJid] = stamp
    }
}
