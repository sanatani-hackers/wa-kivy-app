package kt.sanatani.wapower.jni

import kt.sanatani.wapower.proto.NeonizeProtos
import kt.sanatani.wapower.proto.waCompanionReg.WACompanionReg.DeviceProps
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NeonizeBridgeTest {

    @Test
    fun sanitizePhoneDigits_stripsNonDigits() {
        assertEquals("919876543210", NeonizeBridge.sanitizePhoneDigits("+91 98765-43210"))
        assertEquals("14155552671", NeonizeBridge.sanitizePhoneDigits("(1) 415-555-2671"))
    }

    @Test
    fun parseAllDevicesString_handlesMultipleAndEmpty() {
        assertTrue(NeonizeBridge.parseAllDevicesString("").isEmpty())
        assertTrue(NeonizeBridge.parseAllDevicesString("error: db locked").isEmpty())

        val sample = "919876543210:4@s.whatsapp.net,Alisa,WA Biz,true|\u0001|14155550100:1@s.whatsapp.net,Bob,,false"
        val parsed = NeonizeBridge.parseAllDevicesString(sample)
        assertEquals(2, parsed.size)
        assertEquals("919876543210:4@s.whatsapp.net", parsed[0].jid)
        assertEquals("Alisa", parsed[0].pushName)
        assertEquals("WA Biz", parsed[0].businessName)
        assertTrue(parsed[0].initialized)
        assertFalse(parsed[1].initialized)
    }

    @Test
    fun protobufRoundTrips_devicePropsAndPairPhoneParams() {
        val props = DeviceProps.newBuilder()
            .setOs("WA power (Android)")
            .setPlatformType(DeviceProps.PlatformType.SAFARI)
            .build()
        val parsedProps = DeviceProps.parseFrom(props.toByteArray())
        assertEquals("WA power (Android)", parsedProps.os)
        assertEquals(DeviceProps.PlatformType.SAFARI, parsedProps.platformType)

        val pairParams = NeonizeProtos.PairPhoneParams.newBuilder()
            .setPhone("919876543210")
            .setShowPushNotification(true)
            .setClientType(6)
            .setClientDisplayName("Safari (Linux)")
            .build()
        val parsedParams = NeonizeProtos.PairPhoneParams.parseFrom(pairParams.toByteArray())
        assertEquals("919876543210", parsedParams.phone)
        assertTrue(parsedParams.showPushNotification)
        assertEquals(6, parsedParams.clientType)
        assertEquals("Safari (Linux)", parsedParams.clientDisplayName)
    }
}
