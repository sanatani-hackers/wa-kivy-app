package kt.sanatani.wapower.db

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DbHelpersTest {

    @Test
    fun normalizeJid_stripsDeviceSuffix() {
        assertEquals("919876543210@s.whatsapp.net", normalizeJid("919876543210:12@s.whatsapp.net"))
        assertEquals("1203630123456@g.us", normalizeJid(" 1203630123456@g.us "))
    }

    @Test
    fun receiptRank_isStrictlyMonotonic() {
        assertTrue(ReceiptRank.rankOf("pending") < ReceiptRank.rankOf("sent"))
        assertTrue(ReceiptRank.rankOf("sent") < ReceiptRank.rankOf("delivered"))
        assertTrue(ReceiptRank.rankOf("delivered") < ReceiptRank.rankOf("read"))
        assertTrue(ReceiptRank.rankOf("read") < ReceiptRank.rankOf("played"))
    }
}
