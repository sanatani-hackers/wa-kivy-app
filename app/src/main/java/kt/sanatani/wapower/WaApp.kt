package kt.sanatani.wapower

import android.app.Application
import kt.sanatani.wapower.core.WaClientManager

class WaApp : Application() {
    override fun onCreate() {
        super.onCreate()
        WaClientManager.init(this)
    }
}
