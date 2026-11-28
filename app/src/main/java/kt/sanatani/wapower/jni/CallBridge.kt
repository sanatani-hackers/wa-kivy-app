package kt.sanatani.wapower.jni

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import org.json.JSONObject

data class CallNativeEvent(
    val uuid: String,
    val event: String,
    val callId: String,
    val peer: String,
    val phase: String,
    val rawJson: String,
)

object CallBridge {
    private val _events = MutableSharedFlow<CallNativeEvent>(extraBufferCapacity = 64)
    val events: SharedFlow<CallNativeEvent> = _events.asSharedFlow()

    internal fun dispatchCallEvent(uuid: String, json: String) {
        val parsed = runCatching {
            val obj = JSONObject(json)
            CallNativeEvent(
                uuid = uuid,
                event = obj.optString("event", ""),
                callId = obj.optString("call_id", ""),
                peer = obj.optString("peer", ""),
                phase = obj.optString("phase", ""),
                rawJson = json,
            )
        }.getOrElse {
            CallNativeEvent(
                uuid = uuid,
                event = "parse_error",
                callId = "",
                peer = "",
                phase = "",
                rawJson = json,
            )
        }
        _events.tryEmit(parsed)
    }
}
