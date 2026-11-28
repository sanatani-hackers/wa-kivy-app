package kt.sanatani.wapower

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import kt.sanatani.wapower.core.ConnectionPhase
import kt.sanatani.wapower.core.WaClientManager
import kt.sanatani.wapower.jni.NeonizeBridge
import kt.sanatani.wapower.service.WaForegroundService
import kt.sanatani.wapower.ui.components.MainTab
import kt.sanatani.wapower.ui.components.WaBottomBar
import kt.sanatani.wapower.ui.screens.CallsPlaceholderScreen
import kt.sanatani.wapower.ui.screens.ChatDetailScreen
import kt.sanatani.wapower.ui.screens.ChatsScreen
import kt.sanatani.wapower.ui.screens.ConnectScreen
import kt.sanatani.wapower.ui.screens.ContactsScreen
import kt.sanatani.wapower.ui.screens.SettingsScreen
import kt.sanatani.wapower.ui.screens.ToolsPlaceholderScreen
import kt.sanatani.wapower.ui.theme.WaColors
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
                val repo = WaClientManager.repository
                val activeChats by repo.activeChats.collectAsStateWithLifecycle(initialValue = emptyList())
                val archivedChats by repo.archivedChats.collectAsStateWithLifecycle(initialValue = emptyList())
                val contacts by repo.contacts.collectAsStateWithLifecycle(initialValue = emptyList())

                val scope = rememberCoroutineScope()
                var currentTab by rememberSaveable { mutableStateOf(MainTab.CHATS) }
                var activeChatJid by rememberSaveable { mutableStateOf<String?>(null) }
                var showConnectionPage by rememberSaveable { mutableStateOf(false) }
                var activeCallsJson by rememberSaveable { mutableStateOf("") }

                // Auto-start foreground engine if a saved session already exists
                LaunchedEffect(state.storedDevices.size) {
                    if (state.storedDevices.isNotEmpty() && state.phase == ConnectionPhase.STOPPED) {
                        WaForegroundService.start(this@MainActivity, forceNewDevice = false)
                    }
                }

                val isPairedOrHasSession = state.storedDevices.isNotEmpty() ||
                    state.selfJid != null ||
                    state.phase == ConnectionPhase.CONNECTED ||
                    state.phase == ConnectionPhase.READY

                BackHandler(enabled = activeChatJid != null || (showConnectionPage && isPairedOrHasSession)) {
                    when {
                        activeChatJid != null -> activeChatJid = null
                        showConnectionPage && isPairedOrHasSession -> showConnectionPage = false
                    }
                }

                when {
                    !isPairedOrHasSession || showConnectionPage -> {
                        ConnectScreen(
                            state = state,
                            onStartConnect = { forceNew ->
                                WaForegroundService.start(this@MainActivity, forceNewDevice = forceNew)
                            },
                            onStopConnect = {
                                WaForegroundService.stop(this@MainActivity)
                            },
                            onRequestPairCode = { phone ->
                                WaForegroundService.start(this@MainActivity, forceNewDevice = false)
                                WaClientManager.requestPairingCode(phone)
                            },
                            onLogoutReset = {
                                activeChatJid = null
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

                    activeChatJid != null -> {
                        val jid = activeChatJid!!
                        val chatRow by repo.observeChat(jid).collectAsStateWithLifecycle(initialValue = null)
                        val messages by repo.observeMessages(jid).collectAsStateWithLifecycle(initialValue = emptyList())

                        ChatDetailScreen(
                            chatJid = jid,
                            chatRow = chatRow,
                            messages = messages,
                            ghostConfig = state.ghostConfig,
                            onBack = { activeChatJid = null },
                            onSendText = { text, quotedId ->
                                repo.sendTextMessage(jid, text, quotedId)
                            },
                            onSendMedia = { uri, mime, name, caption ->
                                repo.sendMediaAttachment(jid, uri, mime, name, caption)
                            },
                            onDownloadMedia = { msgId ->
                                repo.downloadMessageMedia(msgId)
                            },
                            onSendReaction = { msgId, senderJid, emoji ->
                                repo.sendReaction(jid, msgId, senderJid, emoji)
                            },
                            onMarkReadManual = {
                                scope.launch { repo.markChatRead(jid, force = true) }
                            },
                            onMarkReadAuto = {
                                scope.launch { repo.markChatRead(jid, force = false) }
                            },
                            onSubscribePresence = {
                                repo.subscribeChatPresence(jid)
                            },
                            onTypingChanged = { composing ->
                                repo.sendTypingIfAllowed(jid, composing)
                            },
                        )
                    }

                    else -> {
                        Scaffold(
                            containerColor = WaColors.Background,
                            bottomBar = {
                                WaBottomBar(
                                    currentTab = currentTab,
                                    onSelectTab = { currentTab = it },
                                )
                            },
                        ) { innerPadding ->
                            Box(
                                modifier = Modifier
                                    .fillMaxSize()
                                    .padding(innerPadding),
                            ) {
                                when (currentTab) {
                                    MainTab.CHATS -> ChatsScreen(
                                        activeChats = activeChats,
                                        archivedChats = archivedChats,
                                        syncState = state.syncState,
                                        onOpenChat = { jid -> activeChatJid = jid },
                                        onTogglePin = { jid, pin ->
                                            scope.launch { repo.setChatPinned(jid, pin) }
                                        },
                                        onToggleArchive = { jid, arch ->
                                            scope.launch { repo.setChatArchived(jid, arch) }
                                        },
                                        onToggleMute = { jid, mute ->
                                            scope.launch { repo.setChatMuted(jid, mute) }
                                        },
                                        onMarkRead = { jid ->
                                            scope.launch { repo.markChatRead(jid, force = true) }
                                        },
                                        onRequestAvatar = { jid ->
                                            repo.fetchProfileAvatarIfNeeded(jid)
                                        },
                                    )

                                    MainTab.CONTACTS -> ContactsScreen(
                                        contacts = contacts,
                                        onResync = { WaClientManager.resyncContacts() },
                                        onCheckOnWhatsApp = { phone -> repo.checkOnWhatsApp(phone) },
                                        onOpenChat = { jid -> activeChatJid = jid },
                                        onPlaceCall = { jid, isVideo ->
                                            scope.launch {
                                                repo.placeCall(jid, isVideo)
                                                activeCallsJson = NeonizeBridge.listActiveCallsJson(WaClientManager.SESSION_UUID)
                                                currentTab = MainTab.CALLS
                                            }
                                        },
                                    )

                                    MainTab.CALLS -> CallsPlaceholderScreen(
                                        activeCallsJson = activeCallsJson,
                                        onRefreshCalls = {
                                            activeCallsJson = NeonizeBridge.listActiveCallsJson(WaClientManager.SESSION_UUID)
                                        },
                                    )

                                    MainTab.TOOLS -> ToolsPlaceholderScreen()

                                    MainTab.SETTINGS -> SettingsScreen(
                                        state = state,
                                        onUpdateGhost = { transform -> WaClientManager.updateGhost(transform) },
                                        onResyncContacts = { WaClientManager.resyncContacts() },
                                        onOpenConnectionView = { showConnectionPage = true },
                                        onLogoutReset = {
                                            activeChatJid = null
                                            showConnectionPage = false
                                            WaClientManager.logoutAndReset()
                                        },
                                    )
                                }
                            }
                        }
                    }
                }
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
