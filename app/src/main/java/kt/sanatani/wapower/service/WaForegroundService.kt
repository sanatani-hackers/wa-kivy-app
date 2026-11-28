package kt.sanatani.wapower.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kt.sanatani.wapower.MainActivity
import kt.sanatani.wapower.R
import kt.sanatani.wapower.core.ConnectionPhase
import kt.sanatani.wapower.core.WaClientManager
import kt.sanatani.wapower.jni.NeonizeBridge

class WaForegroundService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        WaClientManager.init(applicationContext)
        startForeground(NOTIFICATION_ID, buildNotification("Initializing WA power..."))

        serviceScope.launch {
            WaClientManager.state.collect { state ->
                val text = when (state.phase) {
                    ConnectionPhase.STOPPED -> "Engine idle"
                    ConnectionPhase.CONNECTING -> "Connecting to WhatsApp..."
                    ConnectionPhase.AWAITING_PAIRING -> "Waiting for pairing..."
                    ConnectionPhase.CONNECTED -> "Connected (${state.selfJid ?: "WhatsApp"})"
                    ConnectionPhase.READY -> "Online (${state.pushName ?: state.selfJid ?: "WA power"})"
                    ConnectionPhase.LOGGED_OUT -> "Logged out"
                    ConnectionPhase.ERROR -> "Error: ${state.lastError ?: "connection issue"}"
                }
                val nm = getSystemService(NotificationManager::class.java)
                nm?.notify(NOTIFICATION_ID, buildNotification(text))
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                WaClientManager.stopEngine()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }

            ACTION_START -> {
                val forceNew = intent.getBooleanExtra(EXTRA_NEW_DEVICE, false)
                WaClientManager.startEngine(forceNewDevice = forceNew)
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        NeonizeBridge.stopAll()
        serviceScope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "WA power Engine",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Keeps WhatsApp Web socket and call bridge active"
                setShowBadge(false)
            }
            val nm = getSystemService(NotificationManager::class.java)
            nm?.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(statusText: String): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, WaForegroundService::class.java).apply { action = ACTION_STOP },
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("WA power")
            .setContentText(statusText)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(openIntent)
            .addAction(0, "Stop", stopIntent)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "wa_power_engine"
        private const val NOTIFICATION_ID = 1001
        const val ACTION_START = "kt.sanatani.wapower.action.START"
        const val ACTION_STOP = "kt.sanatani.wapower.action.STOP"
        const val EXTRA_NEW_DEVICE = "extra_new_device"

        fun start(context: Context, forceNewDevice: Boolean = false) {
            val intent = Intent(context, WaForegroundService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_NEW_DEVICE, forceNewDevice)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            val intent = Intent(context, WaForegroundService::class.java).apply {
                action = ACTION_STOP
            }
            context.startService(intent)
        }
    }
}
