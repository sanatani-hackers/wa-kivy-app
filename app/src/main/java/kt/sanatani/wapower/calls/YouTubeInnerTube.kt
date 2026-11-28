package kt.sanatani.wapower.calls

import okhttp3.ConnectionPool
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit

data class YtSearchHit(
    val id: String,
    val title: String,
    val author: String,
    val durationText: String,
    val durationSec: Int,
    val isLive: Boolean,
    val url: String = "https://youtu.be/$id",
)

data class YtStream(
    val itag: Int,
    val url: String,
    val mimeType: String,
    val codecs: String,
    val bitrate: Int,
    val contentLength: Long,
    val hasAudio: Boolean,
    val hasVideo: Boolean,
    val client: String,
    val headers: Map<String, String>,
    val width: Int = 0,
    val height: Int = 0,
    val fps: Int = 0,
) {
    val audioOnly: Boolean get() = hasAudio && !hasVideo
    val muxed: Boolean get() = hasAudio && hasVideo
    val kbps: Int get() = if (bitrate > 0) (bitrate / 1000) else 0
    val ext: String
        get() {
            val base = mimeType.substringBefore(';').trim().lowercase()
            return when (base) {
                "audio/mp4" -> "m4a"
                "video/mp4" -> "mp4"
                "audio/webm", "video/webm" -> "webm"
                "audio/3gpp", "video/3gpp" -> "3gp"
                else -> "bin"
            }
        }
}

data class YtVideoInfo(
    val videoId: String,
    val title: String,
    val author: String,
    val durationSec: Int,
    val isLive: Boolean,
    val streams: List<YtStream>,
    val client: String,
    val fetchedAtMs: Long = System.currentTimeMillis(),
    val expiresAtMs: Long = 0L,
)

data class YtResolvedAudio(
    val videoId: String,
    val title: String,
    val author: String,
    val durationSec: Int,
    val client: String,
    val url: String,
    val headersJson: String,
    val contentLength: Long,
    val kbps: Int,
)

data class YtResolvedAv(
    val videoId: String,
    val title: String,
    val author: String,
    val durationSec: Int,
    val client: String,
    val videoUrl: String,
    val audioUrl: String,
    val headersJson: String,
    val videoContentLength: Long,
    val audioContentLength: Long,
    val qualityLabel: String,
    val fps: Int,
)

class YouTubeInnerTube(
    private val http: OkHttpClient = defaultOkHttpClient(),
) {
    private data class ClientCfg(
        val name: String,
        val clientId: String,
        val userAgent: String,
        val context: Map<String, Any>,
        val extraHeaders: Map<String, String> = emptyMap(),
    ) {
        fun version(): String = context["clientVersion"]?.toString().orEmpty()

        fun mediaHeaders(): Map<String, String> {
            val map = LinkedHashMap<String, String>()
            map["User-Agent"] = userAgent
            extraHeaders["Origin"]?.let { map["Origin"] = it }
            extraHeaders["Referer"]?.let { map["Referer"] = it }
            return map
        }
    }

    private val videoCache = ConcurrentHashMap<String, YtVideoInfo>()
    private val excludedClients = ConcurrentHashMap<String, ConcurrentHashMap<String, Long>>()

    @Volatile
    private var visitorData: String? = null

    @Volatile
    private var visitorFetchedMs: Long = 0L

    fun search(query: String, limit: Int = 6): List<YtSearchHit> {
        val bodyJson = JSONObject().apply {
            put(
                "context",
                JSONObject().put(
                    "client",
                    JSONObject().apply {
                        put("clientName", "WEB")
                        put("clientVersion", "2.20260708.00.00")
                        put("hl", "en")
                        put("gl", "US")
                    },
                ),
            )
            put("query", query)
        }
        val req = Request.Builder()
            .url(SEARCH_URL)
            .post(bodyJson.toString().toRequestBody(JSON_MEDIA_TYPE))
            .header("User-Agent", CHROME_UA)
            .header("Accept", "application/json")
            .header("Cache-Control", "no-cache")
            .header("X-Goog-Api-Format-Version", "1")
            .header("X-Origin", "https://www.youtube.com")
            .header("Referer", "https://www.youtube.com/")
            .build()

        val root = executeJsonWithRetry(req, "YouTube search")

        val hits = ArrayList<YtSearchHit>()
        val seen = HashSet<String>()
        walkSearchResults(root, hits, seen, limit.coerceAtLeast(10))
        return hits.take(limit)
    }

    fun resolveAudioStream(
        queryOrUrl: String,
        maxKbps: Int = 160,
        maxMinutes: Int = 60,
    ): YtResolvedAudio {
        val vid = resolveVideoId(queryOrUrl)
        val info = extract(vid)
        if (info.isLive) throw IllegalStateException("'${info.title}' is a live stream")
        if (maxMinutes > 0 && info.durationSec > maxMinutes * 60) {
            throw IllegalStateException("'${info.title}' exceeds ${maxMinutes}m limit")
        }
        val stream = selectAudioStream(info.streams, maxKbps)
            ?: throw IllegalStateException("No audio stream available for ${info.videoId}")
        return YtResolvedAudio(
            videoId = info.videoId,
            title = info.title,
            author = info.author,
            durationSec = info.durationSec,
            client = stream.client,
            url = stream.url,
            headersJson = JSONObject(stream.headers).toString(),
            contentLength = stream.contentLength,
            kbps = stream.kbps,
        )
    }

    fun resolveAvStream(
        queryOrUrl: String,
        maxHeight: Int = 480,
        maxKbps: Int = 160,
        maxMinutes: Int = 15,
    ): YtResolvedAv {
        val vid = resolveVideoId(queryOrUrl)
        val info = extract(vid)
        if (info.isLive) throw IllegalStateException("'${info.title}' is a live stream")
        if (maxMinutes > 0 && info.durationSec > maxMinutes * 60) {
            throw IllegalStateException("'${info.title}' exceeds ${maxMinutes}m video limit")
        }

        val (vStream, aStream) = selectAvPair(info.streams, maxHeight, maxKbps)
        val effectiveAudioUrl = aStream?.url ?: vStream.url
        val effectiveAudioLen = aStream?.contentLength ?: vStream.contentLength
        val mergedHeaders = LinkedHashMap<String, String>()
        mergedHeaders.putAll(vStream.headers)
        aStream?.headers?.let { mergedHeaders.putAll(it) }

        val label = if (vStream.height > 0) "${vStream.height}p" else if (maxHeight <= 360) "360p" else "480p"
        return YtResolvedAv(
            videoId = info.videoId,
            title = info.title,
            author = info.author,
            durationSec = info.durationSec,
            client = vStream.client,
            videoUrl = vStream.url,
            audioUrl = effectiveAudioUrl,
            headersJson = JSONObject(mergedHeaders).toString(),
            videoContentLength = vStream.contentLength,
            audioContentLength = effectiveAudioLen,
            qualityLabel = label,
            fps = if (vStream.fps in 1..30) vStream.fps else 15,
        )
    }

    fun reportRefused(videoId: String, clientName: String, ttlMs: Long = 10 * 60_000L) {
        excludedClients.getOrPut(videoId) { ConcurrentHashMap() }[clientName] =
            System.currentTimeMillis() + ttlMs
        val cached = videoCache[videoId]
        if (cached != null && cached.client == clientName) {
            videoCache.remove(videoId)
        }
    }

    fun resolveVideoId(input: String): String {
        extractVideoIdOrNull(input)?.let { return it }
        val hits = search(input.trim(), limit = 5)
        if (hits.isEmpty()) throw IllegalStateException("No YouTube results for '$input'")
        val nonLive = hits.firstOrNull { !it.isLive } ?: hits.first()
        return nonLive.id
    }

    fun extract(videoId: String, useCache: Boolean = true): YtVideoInfo {
        val now = System.currentTimeMillis()
        val excluded = activeExclusions(videoId, now)
        if (useCache) {
            val cached = videoCache[videoId]
            if (cached != null && cached.client !in excluded) {
                val deadline = if (cached.expiresAtMs > 0L) {
                    minOf(cached.fetchedAtMs + 4 * 3600_000L, cached.expiresAtMs - 600_000L)
                } else {
                    cached.fetchedAtMs + 4 * 3600_000L
                }
                if (now < deadline) return cached
                videoCache.remove(videoId)
            }
        }

        val reasons = LinkedHashMap<String, String>()
        var visitor = getVisitorData(force = false)
        for (attempt in 0..1) {
            for (name in CASCADE) {
                if (name in excluded) continue
                val cfg = CLIENTS.first { it.name == name }
                try {
                    val info = tryClient(videoId, cfg, visitor)
                    videoCache[videoId] = info
                    return info
                } catch (e: Exception) {
                    reasons[name] = e.message ?: e.javaClass.simpleName
                }
            }
            if (attempt == 0 && reasons.values.any { it.contains("LOGIN_REQUIRED", ignoreCase = true) }) {
                val fresh = getVisitorData(force = true)
                if (fresh == null || fresh == visitor) break
                visitor = fresh
                reasons.clear()
            } else {
                break
            }
        }
        val summary = reasons.entries.joinToString("; ") { "${it.key}: ${it.value}" }
        throw IllegalStateException("Failed resolving $videoId ($summary)")
    }

    private fun tryClient(videoId: String, cfg: ClientCfg, visitor: String?): YtVideoInfo {
        val clientObj = JSONObject(cfg.context).apply {
            put("hl", "en")
            put("gl", "US")
            if (!visitor.isNullOrBlank()) put("visitorData", visitor)
        }
        val body = JSONObject().apply {
            put("videoId", videoId)
            put("contentCheckOk", true)
            put("racyCheckOk", true)
            put("context", JSONObject().put("client", clientObj))
        }

        val reqBuilder = Request.Builder()
            .url(PLAYER_URL)
            .post(body.toString().toRequestBody(JSON_MEDIA_TYPE))
            .header("User-Agent", cfg.userAgent)
            .header("Accept", "application/json")
            .header("Cache-Control", "no-cache")
            .header("X-Goog-Api-Format-Version", "1")
            .header("X-YouTube-Client-Name", cfg.clientId)
            .header("X-YouTube-Client-Version", cfg.version())
        cfg.extraHeaders.forEach { (k, v) -> reqBuilder.header(k, v) }
        if (!visitor.isNullOrBlank()) {
            reqBuilder.header("X-Goog-Visitor-Id", visitor)
        }

        val data = executeJsonWithRetry(reqBuilder.build(), "Player")

        val playability = data.optJSONObject("playabilityStatus") ?: JSONObject()
        val status = playability.optString("status", "")
        if (status != "OK") {
            val reason = playability.optString("reason", "").trim()
            throw IllegalStateException(if (reason.isNotBlank()) "$status: $reason" else status)
        }

        val sdata = data.optJSONObject("streamingData") ?: JSONObject()
        val streams = parseStreams(sdata, cfg)
        if (streams.isEmpty()) {
            throw IllegalStateException("No direct URLs returned")
        }

        val vd = data.optJSONObject("videoDetails") ?: JSONObject()
        val expiresSec = sdata.optLong("expiresInSeconds", 0L)
        val info = YtVideoInfo(
            videoId = videoId,
            title = vd.optString("title", videoId).ifBlank { videoId },
            author = vd.optString("author", ""),
            durationSec = vd.optString("lengthSeconds", "0").toIntOrNull() ?: 0,
            isLive = vd.optBoolean("isLiveContent", false) && (vd.optString("lengthSeconds", "0").toIntOrNull() ?: 0) == 0,
            streams = streams,
            client = cfg.name,
            expiresAtMs = if (expiresSec > 0L) System.currentTimeMillis() + expiresSec * 1000L else 0L,
        )

        val probe = selectAudioStream(streams, 160) ?: streams.first()
        val rng = if (probe.contentLength > 0L) "${probe.contentLength - 1}-${probe.contentLength - 1}" else "0-1"
        val probeReq = Request.Builder().url(probe.url).get().header("Range", "bytes=$rng").apply {
            probe.headers.forEach { (k, v) -> header(k, v) }
        }.build()
        http.newCall(probeReq).execute().use { resp ->
            if (resp.code != 200 && resp.code != 206) {
                throw IllegalStateException("Probe HTTP ${resp.code}")
            }
        }
        return info
    }

    private fun getVisitorData(force: Boolean): String? {
        val now = System.currentTimeMillis()
        if (!force && !visitorData.isNullOrBlank() && now - visitorFetchedMs < 24 * 3600_000L) {
            return visitorData
        }
        return runCatching {
            val body = JSONObject().put(
                "context",
                JSONObject().put(
                    "client",
                    JSONObject().apply {
                        put("clientName", "WEB")
                        put("clientVersion", "2.20260708.00.00")
                        put("hl", "en")
                        put("gl", "US")
                    },
                ),
            )
            val req = Request.Builder()
                .url(VISITOR_URL)
                .post(body.toString().toRequestBody(JSON_MEDIA_TYPE))
                .header("User-Agent", CHROME_UA)
                .build()
            val json = http.newCall(req).execute().use { resp ->
                JSONObject(resp.body?.string().orEmpty())
            }
            val valStr = json.optJSONObject("responseContext")?.optString("visitorData", "").orEmpty()
            if (valStr.isNotBlank()) {
                visitorData = valStr
                visitorFetchedMs = now
                valStr
            } else {
                null
            }
        }.getOrNull()
    }

    private fun executeJsonWithRetry(req: Request, label: String): JSONObject {
        var lastErr: IOException? = null
        var backoffMs = 350L
        for (attempt in 0 until 3) {
            try {
                http.newCall(req).execute().use { resp ->
                    if (!resp.isSuccessful) {
                        throw IllegalStateException("$label HTTP ${resp.code}")
                    }
                    val raw = resp.body?.string().orEmpty()
                    if (raw.isBlank()) {
                        throw IllegalStateException("$label returned empty body")
                    }
                    return JSONObject(raw)
                }
            } catch (ioe: IOException) {
                lastErr = ioe
                if (attempt < 2) {
                    runCatching { Thread.sleep(backoffMs) }
                    backoffMs *= 2L
                }
            }
        }
        throw lastErr ?: IOException("$label network error")
    }

    private fun activeExclusions(videoId: String, now: Long): Set<String> {
        val map = excludedClients[videoId] ?: return emptySet()
        map.entries.removeIf { it.value <= now }
        return map.keys.toSet()
    }

    companion object {
        private const val PLAYER_URL = "https://www.youtube.com/youtubei/v1/player?prettyPrint=false"
        private const val SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
        private const val VISITOR_URL = "https://www.youtube.com/youtubei/v1/visitor_id?prettyPrint=false"
        private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

        private const val SAFARI_VISION_UA =
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
        private const val CHROME_UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"

        private val CASCADE = listOf("VISIONOS", "ANDROID_VR", "TVHTML5", "WEB_REMIX", "IOS")
        private val URL_ID_REGEX = Regex("(?:v=|/v/|/embed/|/shorts/|/live/|youtu\\.be/)([A-Za-z0-9_-]{11})")
        private val PLAIN_ID_REGEX = Regex("^[A-Za-z0-9_-]{11}$")
        private val CODECS_REGEX = Regex("codecs=\"([^\"]*)\"")

        private val CLIENTS = listOf(
            ClientCfg(
                name = "VISIONOS",
                clientId = "101",
                userAgent = SAFARI_VISION_UA,
                context = mapOf(
                    "clientName" to "VISIONOS",
                    "clientVersion" to "1.02",
                    "deviceMake" to "Apple",
                    "deviceModel" to "RealityDevice17,1",
                    "userAgent" to SAFARI_VISION_UA,
                    "osName" to "visionOS",
                    "osVersion" to "26.5.23O471",
                ),
            ),
            ClientCfg(
                name = "ANDROID_VR",
                clientId = "28",
                userAgent = "com.google.android.apps.youtube.vr.oculus/1.65.10 (Linux; U; Android 12L; eureka-user Build/SQ3A.220605.009.A1) gzip",
                context = mapOf(
                    "clientName" to "ANDROID_VR",
                    "clientVersion" to "1.65.10",
                    "deviceMake" to "Oculus",
                    "deviceModel" to "Quest 3",
                    "osName" to "Android",
                    "osVersion" to "12L",
                    "androidSdkVersion" to 32,
                ),
            ),
            ClientCfg(
                name = "TVHTML5",
                clientId = "7",
                userAgent = "Mozilla/5.0(SMART-TV; Linux; Tizen 4.0.0.2) AppleWebkit/605.1.15 (KHTML, like Gecko) SamsungBrowser/9.2 TV Safari/605.1.15",
                context = mapOf(
                    "clientName" to "TVHTML5",
                    "clientVersion" to "7.20260707.07.00",
                ),
            ),
            ClientCfg(
                name = "WEB_REMIX",
                clientId = "67",
                userAgent = CHROME_UA,
                context = mapOf(
                    "clientName" to "WEB_REMIX",
                    "clientVersion" to "1.20260707.12.00",
                ),
                extraHeaders = mapOf(
                    "Origin" to "https://music.youtube.com",
                    "Referer" to "https://music.youtube.com/",
                ),
            ),
            ClientCfg(
                name = "IOS",
                clientId = "5",
                userAgent = "com.google.ios.youtube/21.26.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)",
                context = mapOf(
                    "clientName" to "IOS",
                    "clientVersion" to "21.26.4",
                    "deviceMake" to "Apple",
                    "deviceModel" to "iPhone16,2",
                    "osName" to "iPhone",
                    "osVersion" to "18.3.2.22D82",
                ),
            ),
        )

        fun defaultOkHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .connectionPool(ConnectionPool(8, 5, TimeUnit.MINUTES))
            .retryOnConnectionFailure(true)
            .connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .build()

        fun extractVideoIdOrNull(input: String): String? {
            val trimmed = input.trim()
            if (PLAIN_ID_REGEX.matches(trimmed)) return trimmed
            return URL_ID_REGEX.find(trimmed)?.groupValues?.getOrNull(1)
        }

        fun parseDurationSec(durationText: String): Int {
            val parts = durationText.trim().split(':').mapNotNull { it.toIntOrNull() }
            if (parts.isEmpty()) return 0
            return parts.fold(0) { acc, v -> acc * 60 + v }
        }

        internal fun selectAudioStream(streams: List<YtStream>, maxKbps: Int = 160): YtStream? {
            val audioOnly = streams.filter { it.audioOnly }
            val m4a = audioOnly.filter { it.ext == "m4a" }
            val candidates = m4a.ifEmpty { audioOnly }.ifEmpty { streams.filter { it.muxed } }
            if (candidates.isEmpty()) return null
            if (maxKbps > 0) {
                val within = candidates.filter { it.bitrate <= maxKbps * 1000 }
                if (within.isNotEmpty()) return within.maxByOrNull { it.bitrate }
                return candidates.minByOrNull { it.bitrate }
            }
            return candidates.maxByOrNull { it.bitrate }
        }

        internal fun selectAvPair(
            streams: List<YtStream>,
            maxHeight: Int = 480,
            maxKbps: Int = 160,
        ): Pair<YtStream, YtStream?> {
            val avcAdaptive = streams.filter { s ->
                s.hasVideo && !s.hasAudio &&
                    s.mimeType.substringBefore(';').trim().equals("video/mp4", ignoreCase = true) &&
                    (s.codecs.contains("avc1", ignoreCase = true) || s.itag in setOf(133, 134, 135, 160)) &&
                    s.height in 1..maxHeight
            }

            val vAdaptive = if (avcAdaptive.isNotEmpty()) {
                if (maxHeight <= 360) {
                    avcAdaptive.firstOrNull { it.itag == 134 }
                } else {
                    avcAdaptive.firstOrNull { it.itag == 135 } ?: avcAdaptive.firstOrNull { it.itag == 134 }
                } ?: avcAdaptive.maxWithOrNull(compareBy({ it.height }, { it.bitrate }))
            } else {
                null
            }

            if (vAdaptive != null) {
                val aStream = streams.firstOrNull { it.itag == 140 && it.audioOnly }
                    ?: selectAudioStream(streams, maxKbps)
                if (aStream != null) return vAdaptive to aStream
            }

            val muxed = streams.filter { s ->
                s.muxed && s.mimeType.substringBefore(';').trim().equals("video/mp4", ignoreCase = true) &&
                    (s.height <= maxHeight || s.itag == 18)
            }
            val chosenMuxed = muxed.firstOrNull { it.itag == 18 }
                ?: muxed.maxWithOrNull(compareBy({ it.height }, { it.bitrate }))
                ?: streams.firstOrNull { it.muxed }
                ?: throw IllegalStateException("No playable <=${maxHeight}p MP4 stream found")
            return chosenMuxed to null
        }

        private fun parseStreams(sdata: JSONObject, cfg: ClientCfg): List<YtStream> {
            val out = ArrayList<YtStream>()
            val hdrs = cfg.mediaHeaders()
            for ((key, isMuxed) in listOf("formats" to true, "adaptiveFormats" to false)) {
                val arr: JSONArray = sdata.optJSONArray(key) ?: continue
                for (i in 0 until arr.length()) {
                    val f = arr.optJSONObject(i) ?: continue
                    val url = f.optString("url", "")
                    if (url.isBlank()) continue
                    val mimeRaw = f.optString("mimeType", "")
                    val mimeBase = mimeRaw.substringBefore(';').trim()
                    val codecs = CODECS_REGEX.find(mimeRaw)?.groupValues?.getOrNull(1).orEmpty()
                    val kind = mimeBase.substringBefore('/')
                    out.add(
                        YtStream(
                            itag = f.optInt("itag", 0),
                            url = url,
                            mimeType = mimeRaw.ifBlank { mimeBase },
                            codecs = codecs,
                            bitrate = f.optInt("bitrate", f.optInt("averageBitrate", 0)),
                            contentLength = f.optString("contentLength", "0").toLongOrNull() ?: 0L,
                            hasAudio = isMuxed || kind == "audio",
                            hasVideo = isMuxed || kind == "video",
                            client = cfg.name,
                            headers = hdrs,
                            width = f.optInt("width", 0),
                            height = f.optInt("height", 0),
                            fps = f.optInt("fps", 0),
                        )
                    )
                }
            }
            return out
        }

        private fun walkSearchResults(
            node: Any?,
            hits: MutableList<YtSearchHit>,
            seen: MutableSet<String>,
            maxCollect: Int,
        ) {
            if (hits.size >= maxCollect) return
            when (node) {
                is JSONObject -> {
                    val vr = node.optJSONObject("videoRenderer")
                    if (vr != null) {
                        val vid = vr.optString("videoId", "")
                        if (vid.isNotBlank() && seen.add(vid)) {
                            val titleRuns = vr.optJSONObject("title")?.optJSONArray("runs")
                            val title = buildString {
                                if (titleRuns != null) {
                                    for (i in 0 until titleRuns.length()) {
                                        append(titleRuns.optJSONObject(i)?.optString("text", "").orEmpty())
                                    }
                                }
                            }.ifBlank { vr.optJSONObject("title")?.optString("simpleText", vid) ?: vid }

                            val authorRuns = vr.optJSONObject("ownerText")?.optJSONArray("runs")
                            val author = buildString {
                                if (authorRuns != null) {
                                    for (i in 0 until authorRuns.length()) {
                                        append(authorRuns.optJSONObject(i)?.optString("text", "").orEmpty())
                                    }
                                }
                            }
                            val durText = vr.optJSONObject("lengthText")?.optString("simpleText", "").orEmpty()
                            val durSec = parseDurationSec(durText)
                            hits.add(
                                YtSearchHit(
                                    id = vid,
                                    title = title.trim(),
                                    author = author.trim(),
                                    durationText = durText.ifBlank { "LIVE" },
                                    durationSec = durSec,
                                    isLive = durText.isBlank(),
                                )
                            )
                        }
                        return
                    }
                    val keys = node.keys()
                    while (keys.hasNext()) {
                        walkSearchResults(node.opt(keys.next()), hits, seen, maxCollect)
                    }
                }
                is JSONArray -> {
                    for (i in 0 until node.length()) {
                        walkSearchResults(node.opt(i), hits, seen, maxCollect)
                    }
                }
            }
        }
    }
}
