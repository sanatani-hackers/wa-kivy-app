package kt.sanatani.wapower.ui

import kt.sanatani.wapower.db.ChatEntity
import kt.sanatani.wapower.db.ChatListRow
import kt.sanatani.wapower.proto.NeonizeProtos
import org.junit.Assert.assertEquals
import org.junit.Test

class Sprint3ModelsTest {

    @Test
    fun chatListRow_resolvesDisplayNamePriority() {
        val row1 = ChatListRow(
            chat = ChatEntity(jid = "919876543210@s.whatsapp.net", name = "Group Title"),
            contactFullName = "Full Name",
            contactPushName = "Push Name",
        )
        assertEquals("Group Title", row1.displayName)

        val row2 = ChatListRow(
            chat = ChatEntity(jid = "919876543210@s.whatsapp.net", name = ""),
            contactFullName = "Full Name",
            contactPushName = "Push Name",
        )
        assertEquals("Full Name", row2.displayName)

        val row3 = ChatListRow(
            chat = ChatEntity(jid = "919876543210@s.whatsapp.net", name = ""),
            contactFullName = "",
            contactPushName = "Push Name",
        )
        assertEquals("Push Name", row3.displayName)
    }

    @Test
    fun getProfilePictureParams_serializesPreviewFlag() {
        val params = NeonizeProtos.GetProfilePictureParams.newBuilder()
            .setPreview(true)
            .build()
        val parsed = NeonizeProtos.GetProfilePictureParams.parseFrom(params.toByteArray())
        assertEquals(true, parsed.preview)
    }
}
