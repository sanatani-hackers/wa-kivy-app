package kt.sanatani.wapower.core

import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.FutureProofMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.ImageMessage
import kt.sanatani.wapower.proto.waE2E.WAWebProtobufsE2E.Message
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MessagePayloadExtractorTest {

    @Test
    fun unwrapAndExtract_handlesEphemeralViewOnceImage() {
        val innerImg = Message.newBuilder()
            .setImageMessage(
                ImageMessage.newBuilder()
                    .setCaption("Secret pic")
                    .setMimetype("image/jpeg")
                    .build()
            )
            .build()

        val wrapped = Message.newBuilder()
            .setEphemeralMessage(
                FutureProofMessage.newBuilder()
                    .setMessage(
                        Message.newBuilder()
                            .setViewOnceMessageV2(
                                FutureProofMessage.newBuilder()
                                    .setMessage(innerImg)
                                    .build()
                            )
                            .build()
                    )
                    .build()
            )
            .build()

        val extracted = MessagePayloadExtractor.extractPayload(wrapped)
        assertNotNull(extracted)
        assertEquals("image", extracted!!.msgType)
        assertEquals("Secret pic", extracted.body)
        assertEquals("image/jpeg", extracted.mediaMime)
        assertTrue(extracted.isViewOnce)
    }

    @Test
    fun normalizeTimestampSec_convertsMillisToSeconds() {
        assertEquals(1700000000L, MessagePayloadExtractor.normalizeTimestampSec(1700000000L))
        assertEquals(1700000000L, MessagePayloadExtractor.normalizeTimestampSec(1700000000123L))
        assertEquals(0L, MessagePayloadExtractor.normalizeTimestampSec(-5L))
    }
}
