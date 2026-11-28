package kt.sanatani.wapower.core

import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.Message as E2EMessage

data class ExtractedMessagePayload(
    val msgType: String,
    val body: String,
    val preview: String,
    val mediaMime: String,
    val quotedId: String,
    val isViewOnce: Boolean,
    val mediaProto: E2EMessage?,
    val fileName: String,
)

object MessagePayloadExtractor {

    private val MIME_EXT_MAP = mapOf(
        "image/jpeg" to ".jpg",
        "image/jpg" to ".jpg",
        "image/png" to ".png",
        "image/webp" to ".webp",
        "image/gif" to ".gif",
        "video/mp4" to ".mp4",
        "video/3gpp" to ".3gp",
        "audio/ogg" to ".ogg",
        "audio/mpeg" to ".mp3",
        "audio/mp4" to ".m4a",
        "audio/aac" to ".aac",
        "application/pdf" to ".pdf",
    )

    fun normalizeTimestampSec(ts: Long): Long {
        if (ts <= 0L) return 0L
        return if (ts > 10_000_000_000L) ts / 1000L else ts
    }

    fun extForMedia(msgType: String, mime: String, fileName: String = ""): String {
        val dot = fileName.lastIndexOf('.')
        if (dot in 1 until fileName.length - 1) {
            return fileName.substring(dot)
        }
        val baseMime = mime.substringBefore(';').trim().lowercase()
        MIME_EXT_MAP[baseMime]?.let { return it }
        return when (msgType) {
            "image" -> ".jpg"
            "video" -> ".mp4"
            "audio" -> ".ogg"
            "sticker" -> ".webp"
            else -> ".bin"
        }
    }

    fun unwrapMessage(msg: E2EMessage?): Pair<E2EMessage?, Boolean> {
        var cur = msg
        var isViewOnce = false
        repeat(4) {
            val m = cur ?: return@repeat
            cur = when {
                m.hasViewOnceMessage() -> {
                    isViewOnce = true
                    m.viewOnceMessage.message
                }
                m.hasViewOnceMessageV2() -> {
                    isViewOnce = true
                    m.viewOnceMessageV2.message
                }
                m.hasViewOnceMessageV2Extension() -> {
                    isViewOnce = true
                    m.viewOnceMessageV2Extension.message
                }
                m.hasEphemeralMessage() -> m.ephemeralMessage.message
                m.hasDocumentWithCaptionMessage() -> m.documentWithCaptionMessage.message
                m.hasDeviceSentMessage() -> m.deviceSentMessage.message
                m.hasEditedMessage() -> m.editedMessage.message
                else -> return cur to isViewOnce
            }
        }
        return cur to isViewOnce
    }

    fun extractText(msg: E2EMessage?): String {
        val (inner, _) = unwrapMessage(msg)
        if (inner == null) return ""
        if (inner.hasConversation() && inner.conversation.isNotEmpty()) {
            return inner.conversation
        }
        if (inner.hasExtendedTextMessage() && inner.extendedTextMessage.text.isNotEmpty()) {
            return inner.extendedTextMessage.text
        }
        if (inner.hasImageMessage()) {
            return inner.imageMessage.caption.ifBlank { "[Photo]" }
        }
        if (inner.hasVideoMessage()) {
            return inner.videoMessage.caption.ifBlank { "[Video]" }
        }
        if (inner.hasAudioMessage()) {
            return "[Audio]"
        }
        if (inner.hasDocumentMessage()) {
            val cap = inner.documentMessage.caption
            val fn = inner.documentMessage.fileName
            return cap.ifBlank { if (fn.isNotBlank()) fn else "[Document]" }
        }
        if (inner.hasStickerMessage()) {
            return "[Sticker]"
        }
        return ""
    }

    fun extractPayload(msg: E2EMessage?): ExtractedMessagePayload? {
        val (inner, wrapperVo) = unwrapMessage(msg)
        if (inner == null) return null

        if (inner.hasConversation() && inner.conversation.isNotEmpty()) {
            val txt = inner.conversation
            return ExtractedMessagePayload(
                msgType = "text",
                body = txt,
                preview = txt,
                mediaMime = "",
                quotedId = "",
                isViewOnce = wrapperVo,
                mediaProto = null,
                fileName = "",
            )
        }

        if (inner.hasExtendedTextMessage()) {
            val et = inner.extendedTextMessage
            val txt = et.text
            val qid = if (et.hasContextInfo()) et.contextInfo.stanzaID else ""
            return ExtractedMessagePayload(
                msgType = "text",
                body = txt,
                preview = txt,
                mediaMime = "",
                quotedId = qid,
                isViewOnce = wrapperVo,
                mediaProto = null,
                fileName = "",
            )
        }

        if (inner.hasImageMessage()) {
            val im = inner.imageMessage
            val vo = wrapperVo || im.viewOnce
            val cap = im.caption
            val label = if (vo) "View-Once Photo" else "Photo"
            val qid = if (im.hasContextInfo()) im.contextInfo.stanzaID else ""
            return ExtractedMessagePayload(
                msgType = "image",
                body = cap.ifBlank { label },
                preview = if (cap.isNotBlank()) "Photo: $cap" else label,
                mediaMime = im.mimetype.ifBlank { "image/jpeg" },
                quotedId = qid,
                isViewOnce = vo,
                mediaProto = inner,
                fileName = "",
            )
        }

        if (inner.hasVideoMessage() || inner.hasPtvMessage()) {
            val vm = if (inner.hasVideoMessage()) inner.videoMessage else inner.ptvMessage
            val vo = wrapperVo || vm.viewOnce
            val cap = vm.caption
            val label = if (vo) "View-Once Video" else "Video"
            val qid = if (vm.hasContextInfo()) vm.contextInfo.stanzaID else ""
            return ExtractedMessagePayload(
                msgType = "video",
                body = cap.ifBlank { label },
                preview = if (cap.isNotBlank()) "Video: $cap" else label,
                mediaMime = vm.mimetype.ifBlank { "video/mp4" },
                quotedId = qid,
                isViewOnce = vo,
                mediaProto = inner,
                fileName = "",
            )
        }

        if (inner.hasAudioMessage()) {
            val am = inner.audioMessage
            val vo = wrapperVo || am.viewOnce
            val sec = am.seconds
            val label = when {
                vo -> "View-Once Voice Note"
                sec > 0 -> "Voice Note (${sec}s)"
                else -> "Voice Note"
            }
            val qid = if (am.hasContextInfo()) am.contextInfo.stanzaID else ""
            return ExtractedMessagePayload(
                msgType = "audio",
                body = label,
                preview = label,
                mediaMime = am.mimetype.ifBlank { "audio/ogg" },
                quotedId = qid,
                isViewOnce = vo,
                mediaProto = inner,
                fileName = "",
            )
        }

        if (inner.hasDocumentMessage()) {
            val dm = inner.documentMessage
            val fn = dm.fileName.ifBlank { "Document" }
            val cap = dm.caption
            val qid = if (dm.hasContextInfo()) dm.contextInfo.stanzaID else ""
            return ExtractedMessagePayload(
                msgType = "document",
                body = if (cap.isNotBlank()) "$fn\n$cap" else fn,
                preview = "Doc: $fn",
                mediaMime = dm.mimetype.ifBlank { "application/octet-stream" },
                quotedId = qid,
                isViewOnce = wrapperVo,
                mediaProto = inner,
                fileName = fn,
            )
        }

        if (inner.hasStickerMessage()) {
            val sm = inner.stickerMessage
            val qid = if (sm.hasContextInfo()) sm.contextInfo.stanzaID else ""
            return ExtractedMessagePayload(
                msgType = "sticker",
                body = "[Sticker]",
                preview = "[Sticker]",
                mediaMime = sm.mimetype.ifBlank { "image/webp" },
                quotedId = qid,
                isViewOnce = false,
                mediaProto = inner,
                fileName = "",
            )
        }

        return null
    }
}
