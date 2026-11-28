package kt.sanatani.wapower

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kt.sanatani.wapower.core.WaClientManager
import kt.sanatani.wapower.service.WaForegroundService
import kt.sanatani.wapower.ui.screens.ConnectScreen
import kt.sanatani.wapower.ui.theme.WaPowerTheme

class MainActivity : ComponentActivity() {

    private val requestNotifPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* Best-effort for foreground notification visibility */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        ensureNotificationPermission()

        setContent {
            WaPowerTheme {
                val state by WaClientManager.state.collectAsStateWithLifecycle()
                ConnectScreen(
                    state = state,
                    onStartConnect = { forceNew ->
                        WaForegroundService.start(this, forceNewDevice = forceNew)
                    },
                    onStopConnect = {
                        WaForegroundService.stop(this)
                    },
                    onRequestPairCode = { phone ->
                        WaForegroundService.start(this, forceNewDevice = false)
                        WaClientManager.requestPairingCode(phone)
                    },
                    onLogoutReset = {
                        WaClientManager.logoutAndReset()
                    },
                    onUpdateGhost = { transform ->
                        WaClientManager.updateGhost(transform)
                    },
                    onResyncContacts = {
                        WaClientManager.resyncContacts()
                    },
                )
            }
        }
    }

    private fun ensureNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                requestNotifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }
}
