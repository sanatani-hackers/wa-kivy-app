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
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Call
import androidx.compose.material.icons.rounded.CallEnd
import androidx.compose.material.icons.rounded.Videocam
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import kt.sanatani.wapower.calls.ActiveCallInfo
import kt.sanatani.wapower.core.ConnectionPhase
import kt.sanatani.wapower.core.WaClientManager
import kt.sanatani.wapower.service.WaForegroundService
import kt.sanatani.wapower.ui.components.MainTab
import kt.sanatani.wapower.ui.components.WaBottomBar
import kt.sanatani.wapower.ui.screens.CallsScreen
import kt.sanatani.wapower.ui.screens.ChatDetailScreen
import kt.sanatani.wapower.ui.screens.ChatsScreen
import kt.sanatani.wapower.ui.screens.ConnectScreen
import kt.sanatani.wapower.ui.screens.ContactsScreen
import kt.sanatani.wapower.ui.screens.SettingsScreen
import kt.sanatani.wapower.ui.screens.ToolsPlaceholderScreen
import kt.sanatani.wapower.ui.theme.WaColors
import kt.sanatani.wapower.ui.theme.WaPowerTheme
import kt.sanatani.wapower.ui.theme.WaShapes

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
                val callController = WaClientManager.callController
                val activeChats by repo.activeChats.collectAsStateWithLifecycle(initialValue = emptyList())
                val archivedChats by repo.archivedChats.collectAsStateWithLifecycle(initialValue = emptyList())
                val contacts by repo.contacts.collectAsStateWithLifecycle(initialValue = emptyList())
                val callDeck by callController.deckState.collectAsStateWithLifecycle()
                val callHistory by callController.callHistory.collectAsStateWithLifecycle(initialValue = emptyList())

                val scope = rememberCoroutineScope()
                var currentTab by rememberSaveable { mutableStateOf(MainTab.CHATS) }
                var activeChatJid by rememberSaveable { mutableStateOf<String?>(null) }
                var showConnectionPage by rememberSaveable { mutableStateOf(false) }

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

                LaunchedEffect(currentTab) {
                    if (currentTab == MainTab.CALLS) {
                        callController.reconcileActiveCalls()
                    }
                }

                val incomingCall = callDeck.activeCalls.firstOrNull {
                    it.direction == "in" && it.phase !in setOf("active", "ended")
                }

                Box(modifier = Modifier.fillMaxSize()) {
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
                                            callController.placeCall(jid, isVideo)
                                            currentTab = MainTab.CALLS
                                        },
                                    )

                                    MainTab.CALLS -> CallsScreen(
                                        deck = callDeck,
                                        callHistory = callHistory,
                                        onPlaceCall = { target, isVideo -> callController.placeCall(target, isVideo) },
                                        onAnswerCall = { cid -> callController.answerCall(cid) },
                                        onAnswerWithMusic = { cid ->
                                            val q = callDeck.queue.firstOrNull()?.queryOrUrl ?: "lofi hip hop beats"
                                            callController.answerCall(cid, withAutoMusicQuery = q)
                                        },
                                        onDeclineCall = { cid -> callController.declineCall(cid) },
                                        onHangupCall = { cid -> callController.hangupCall(cid) },
                                        onPauseAudio = { cid -> callController.pauseAudio(cid) },
                                        onResumeAudio = { cid -> callController.resumeAudio(cid) },
                                        onStopMedia = { cid -> callController.stopMedia(cid) },
                                        onSkipNext = { cid -> callController.skipNext(cid) },
                                        onSearchYouTube = { q -> callController.searchYouTube(q) },
                                        onStreamYtAudio = { cid, q -> callController.streamYouTubeAudio(cid, q) },
                                        onStreamYtVideo = { cid, q -> callController.streamYouTubeVideo(cid, q) },
                                        onEnqueueYt = { url, title, asVid -> callController.enqueueTrack(url, title, asVid) },
                                        onRemoveQueued = { uid -> callController.removeQueuedTrack(uid) },
                                        onPlayLocalMedia = { cid, uri, mime, name -> callController.playLocalMediaFile(cid, uri, mime, name) },
                                        onSetBufferProfile = { callController.setBufferProfile(it) },
                                        onSetVideoQuality = { callController.setVideoQuality(it) },
                                        onSetLoopTrack = { callController.setLoopTrack(it) },
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

                    if (incomingCall != null) {
                        IncomingCallOverlayBanner(
                            call = incomingCall,
                            modifier = Modifier
                                .align(Alignment.TopCenter)
                                .statusBarsPadding()
                                .padding(horizontal = 16.dp, vertical = 10.dp),
                            onOpenDeck = {
                                activeChatJid = null
                                showConnectionPage = false
                                currentTab = MainTab.CALLS
                            },
                            onAnswer = { callController.answerCall(incomingCall.callId) },
                            onAnswerWithMusic = {
                                val q = callDeck.queue.firstOrNull()?.queryOrUrl ?: "lofi hip hop beats"
                                callController.answerCall(incomingCall.callId, withAutoMusicQuery = q)
                            },
                            onDecline = { callController.declineCall(incomingCall.callId) },
                        )
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

@androidx.compose.runtime.Composable
private fun IncomingCallOverlayBanner(
    call: ActiveCallInfo,
    modifier: Modifier = Modifier,
    onOpenDeck: () -> Unit,
    onAnswer: () -> Unit,
    onAnswerWithMusic: () -> Unit,
    onDecline: () -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = WaColors.SurfaceElevated),
        border = BorderStroke(WaShapes.BorderWidth, WaColors.Primary),
        shape = RoundedCornerShape(WaShapes.CardRadius),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Icon(
                        imageVector = if (call.isVideo) Icons.Rounded.Videocam else Icons.Rounded.Call,
                        contentDescription = null,
                        tint = WaColors.Primary,
                        modifier = Modifier.size(18.dp),
                    )
                    Column {
                        Text(
                            text = call.peerDisplay.ifBlank { call.peer },
                            style = MaterialTheme.typography.titleMedium,
                            color = WaColors.TextPrimary,
                        )
                        Text(
                            text = if (call.isVideo) "Incoming video call • ${call.phase}" else "Incoming voice call • ${call.phase}",
                            style = MaterialTheme.typography.labelMedium,
                            color = WaColors.Primary,
                        )
                    }
                }
                TextButton(onClick = onOpenDeck) {
                    Text("Deck", color = WaColors.TextSecondary)
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(
                    onClick = onAnswer,
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = WaColors.ButtonBackground,
                        contentColor = WaColors.ButtonText,
                    ),
                    shape = RoundedCornerShape(WaShapes.ButtonRadius),
                ) {
                    Text("Answer")
                }
                OutlinedButton(
                    onClick = onAnswerWithMusic,
                    modifier = Modifier.weight(1f),
                    border = BorderStroke(1.dp, WaColors.Primary.copy(alpha = 0.6f)),
                    shape = RoundedCornerShape(WaShapes.ButtonRadius),
                ) {
                    Text("+Music", color = WaColors.Primary)
                }
                OutlinedButton(
                    onClick = onDecline,
                    modifier = Modifier.weight(1f),
                    border = BorderStroke(1.dp, WaColors.Danger.copy(alpha = 0.6f)),
                    shape = RoundedCornerShape(WaShapes.ButtonRadius),
                ) {
                    Icon(Icons.Rounded.CallEnd, contentDescription = null, tint = WaColors.Danger, modifier = Modifier.size(16.dp))
                }
            }
        }
    }
}
