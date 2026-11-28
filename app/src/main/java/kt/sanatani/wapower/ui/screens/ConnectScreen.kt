package kt.sanatani.wapower.ui.screens

import android.graphics.Bitmap
import android.graphics.Color
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Bolt
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.ContentCopy
import androidx.compose.material.icons.rounded.LinkOff
import androidx.compose.material.icons.rounded.PhoneAndroid
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.QrCode2
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material.icons.rounded.Sync
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults.SecondaryIndicator
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
import androidx.compose.material3.VerticalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kt.sanatani.wapower.core.ConnectionPhase
import kt.sanatani.wapower.core.GhostConfig
import kt.sanatani.wapower.core.SyncState
import kt.sanatani.wapower.core.WaUiState
import kt.sanatani.wapower.ui.theme.WaColors
import kt.sanatani.wapower.ui.theme.WaShapes

@Composable
fun ConnectScreen(
    state: WaUiState,
    onStartConnect: (forceNew: Boolean) -> Unit,
    onStopConnect: () -> Unit,
    onRequestPairCode: (String) -> Unit,
    onLogoutReset: () -> Unit,
    onUpdateGhost: ((GhostConfig) -> GhostConfig) -> Unit,
    onResyncContacts: () -> Unit,
) {
    var selectedTab by rememberSaveable { mutableIntStateOf(0) }
    var phoneInput by rememberSaveable { mutableStateOf("") }
    val clipboard = LocalClipboardManager.current

    Scaffold(
        containerColor = WaColors.Background,
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            HeaderBanner(state = state)

            StatusCard(
                state = state,
                onStartConnect = onStartConnect,
                onStopConnect = onStopConnect,
                onLogoutReset = onLogoutReset,
            )

            if (state.phase != ConnectionPhase.CONNECTED && state.phase != ConnectionPhase.READY) {
                PairingCard(
                    state = state,
                    selectedTab = selectedTab,
                    onSelectTab = { selectedTab = it },
                    phoneInput = phoneInput,
                    onPhoneInputChange = { phoneInput = it },
                    onRequestPairCode = onRequestPairCode,
                    onCopyCode = { clipboard.setText(AnnotatedString(it)) },
                )
            }

            SyncAndGhostPanel(
                state = state,
                onUpdateGhost = onUpdateGhost,
                onResyncContacts = onResyncContacts,
            )

            EngineLogPanel(logs = state.logs)
        }
    }
}

@Composable
private fun HeaderBanner(state: WaUiState) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(40.dp)
                    .clip(RoundedCornerShape(WaShapes.InputRadius))
                    .background(WaColors.SurfaceElevated)
                    .border(WaShapes.BorderWidth, WaColors.CardBorder, RoundedCornerShape(WaShapes.InputRadius)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = Icons.Rounded.Bolt,
                    contentDescription = null,
                    tint = WaColors.Primary,
                    modifier = Modifier.size(20.dp),
                )
            }
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    text = "WA power",
                    style = MaterialTheme.typography.headlineMedium,
                    color = WaColors.TextPrimary,
                )
                Text(
                    text = "Core ${state.nativeVersion.ifBlank { "initializing" }}",
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.TextMuted,
                )
            }
        }
        StatusSignal(phase = state.phase)
    }
}

@Composable
private fun StatusSignal(phase: ConnectionPhase) {
    val (label, color) = when (phase) {
        ConnectionPhase.STOPPED -> "Idle" to WaColors.TextMuted
        ConnectionPhase.CONNECTING -> "Connecting" to WaColors.Warning
        ConnectionPhase.AWAITING_PAIRING -> "Waiting for link" to WaColors.Info
        ConnectionPhase.CONNECTED -> "Connected" to WaColors.Success
        ConnectionPhase.READY -> "Ready" to WaColors.Primary
        ConnectionPhase.LOGGED_OUT -> "Logged out" to WaColors.Danger
        ConnectionPhase.ERROR -> "Error" to WaColors.Danger
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .clip(CircleShape)
                .background(color),
        )
        Text(
            text = label,
            style = MaterialTheme.typography.labelMedium,
            color = WaColors.TextSecondary,
        )
    }
}

@Composable
private fun StatusCard(
    state: WaUiState,
    onStartConnect: (Boolean) -> Unit,
    onStopConnect: () -> Unit,
    onLogoutReset: () -> Unit,
) {
    val isRunning = state.phase != ConnectionPhase.STOPPED &&
        state.phase != ConnectionPhase.LOGGED_OUT &&
        state.phase != ConnectionPhase.ERROR

    Card(
        colors = CardDefaults.cardColors(containerColor = WaColors.CardBackground),
        border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
        shape = RoundedCornerShape(WaShapes.CardRadius),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(
                    modifier = Modifier.weight(1f),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    Text(
                        text = when {
                            state.selfJid != null ->
                                state.pushName?.takeIf { it.isNotBlank() } ?: state.selfJid
                            state.storedDevices.isNotEmpty() ->
                                "Saved session"
                            else -> "No linked device"
                        },
                        style = MaterialTheme.typography.titleMedium,
                        color = WaColors.TextPrimary,
                    )
                    val subline = state.selfJid ?: state.storedDevices.firstOrNull()?.jid
                    if (subline != null) {
                        Text(
                            text = subline,
                            style = MaterialTheme.typography.labelMedium,
                            color = WaColors.TextSecondary,
                        )
                    }
                    if (!state.lastError.isNullOrBlank()) {
                        Text(
                            text = state.lastError,
                            style = MaterialTheme.typography.bodyMedium,
                            color = WaColors.Danger,
                        )
                    }
                }
                if (state.phase == ConnectionPhase.CONNECTED || state.phase == ConnectionPhase.READY) {
                    Icon(
                        imageVector = Icons.Rounded.CheckCircle,
                        contentDescription = null,
                        tint = WaColors.Primary,
                        modifier = Modifier.size(20.dp),
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                if (!isRunning) {
                    Button(
                        onClick = { onStartConnect(false) },
                        modifier = Modifier
                            .weight(1f)
                            .height(44.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = WaColors.ButtonBackground,
                            contentColor = WaColors.ButtonText,
                        ),
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                    ) {
                        Icon(
                            Icons.Rounded.PlayArrow,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text(
                            text = if (state.storedDevices.isNotEmpty()) "Reconnect" else "Connect",
                            fontWeight = FontWeight.Medium,
                        )
                    }
                } else {
                    OutlinedButton(
                        onClick = onStopConnect,
                        modifier = Modifier
                            .weight(1f)
                            .height(44.dp),
                        border = BorderStroke(WaShapes.BorderWidth, WaColors.Border),
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                    ) {
                        Icon(
                            Icons.Rounded.Stop,
                            contentDescription = null,
                            tint = WaColors.TextSecondary,
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("Disconnect", color = WaColors.TextPrimary, fontWeight = FontWeight.Medium)
                    }
                }

                if (state.storedDevices.isNotEmpty() || state.selfJid != null) {
                    OutlinedButton(
                        onClick = onLogoutReset,
                        modifier = Modifier.height(44.dp),
                        border = BorderStroke(WaShapes.BorderWidth, WaColors.Danger.copy(alpha = 0.45f)),
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                    ) {
                        Icon(
                            Icons.Rounded.LinkOff,
                            contentDescription = null,
                            tint = WaColors.Danger,
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("Unlink", color = WaColors.Danger, fontWeight = FontWeight.Medium)
                    }
                }
            }
        }
    }
}

@Composable
private fun PairingCard(
    state: WaUiState,
    selectedTab: Int,
    onSelectTab: (Int) -> Unit,
    phoneInput: String,
    onPhoneInputChange: (String) -> Unit,
    onRequestPairCode: (String) -> Unit,
    onCopyCode: (String) -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = WaColors.CardBackground),
        border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
        shape = RoundedCornerShape(WaShapes.CardRadius),
    ) {
        Column(modifier = Modifier.fillMaxWidth()) {
            TabRow(
                selectedTabIndex = selectedTab,
                containerColor = WaColors.BackgroundSecondary,
                contentColor = WaColors.Primary,
                indicator = { tabPositions ->
                    SecondaryIndicator(
                        modifier = Modifier.tabIndicatorOffset(tabPositions[selectedTab]),
                        color = WaColors.Primary,
                    )
                },
                divider = { HorizontalDivider(color = WaColors.Divider) },
            ) {
                Tab(
                    selected = selectedTab == 0,
                    onClick = { onSelectTab(0) },
                    selectedContentColor = WaColors.Primary,
                    unselectedContentColor = WaColors.TextSecondary,
                    text = { Text("Phone code") },
                    icon = { Icon(Icons.Rounded.PhoneAndroid, contentDescription = null, modifier = Modifier.size(18.dp)) },
                )
                Tab(
                    selected = selectedTab == 1,
                    onClick = { onSelectTab(1) },
                    selectedContentColor = WaColors.Primary,
                    unselectedContentColor = WaColors.TextSecondary,
                    text = { Text("QR link") },
                    icon = { Icon(Icons.Rounded.QrCode2, contentDescription = null, modifier = Modifier.size(18.dp)) },
                )
            }

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                if (selectedTab == 0) {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(
                            text = "Link with 8-digit pairing code",
                            style = MaterialTheme.typography.titleMedium,
                            color = WaColors.TextPrimary,
                        )
                        Text(
                            text = "Include country code without '+' (e.g. 919876543210)",
                            style = MaterialTheme.typography.bodyMedium,
                            color = WaColors.TextSecondary,
                        )
                    }

                    OutlinedTextField(
                        value = phoneInput,
                        onValueChange = onPhoneInputChange,
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        label = { Text("WhatsApp number") },
                        placeholder = { Text("919876543210") },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                        shape = RoundedCornerShape(WaShapes.InputRadius),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedContainerColor = WaColors.InputBackground,
                            unfocusedContainerColor = WaColors.InputBackground,
                            focusedBorderColor = WaColors.Primary,
                            unfocusedBorderColor = WaColors.InputBorder,
                            focusedLabelColor = WaColors.PrimaryLight,
                            unfocusedLabelColor = WaColors.TextSecondary,
                            focusedTextColor = WaColors.TextPrimary,
                            unfocusedTextColor = WaColors.TextPrimary,
                        ),
                    )

                    Button(
                        onClick = { onRequestPairCode(phoneInput) },
                        enabled = !state.pairingBusy,
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(44.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = WaColors.ButtonBackground,
                            contentColor = WaColors.ButtonText,
                        ),
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                    ) {
                        if (state.pairingBusy) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp,
                                color = WaColors.ButtonText,
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("Requesting code", fontWeight = FontWeight.Medium)
                        } else {
                            Text("Get pairing code", fontWeight = FontWeight.Medium)
                        }
                    }

                    if (!state.pairingError.isNullOrBlank()) {
                        Text(
                            text = state.pairingError,
                            style = MaterialTheme.typography.bodyMedium,
                            color = WaColors.Danger,
                        )
                    }

                    if (!state.pairingCode.isNullOrBlank()) {
                        Surface(
                            color = WaColors.SurfaceVariant,
                            shape = RoundedCornerShape(WaShapes.InputRadius),
                            border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(horizontal = 16.dp, vertical = 12.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween,
                            ) {
                                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                    Text(
                                        text = "Enter in WhatsApp -> Linked devices",
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = WaColors.TextSecondary,
                                    )
                                    Text(
                                        text = state.pairingCode,
                                        style = TextStyle(
                                            fontFamily = FontFamily.Monospace,
                                            fontWeight = FontWeight.SemiBold,
                                            fontSize = 24.sp,
                                            letterSpacing = 2.sp,
                                            fontFeatureSettings = "tnum",
                                        ),
                                        color = WaColors.PrimaryLight,
                                    )
                                }
                                IconButton(onClick = { onCopyCode(state.pairingCode) }) {
                                    Icon(
                                        Icons.Rounded.ContentCopy,
                                        contentDescription = "Copy pairing code",
                                        tint = WaColors.TextSecondary,
                                        modifier = Modifier.size(18.dp),
                                    )
                                }
                            }
                        }
                    }
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(
                            text = "Link with QR code",
                            style = MaterialTheme.typography.titleMedium,
                            color = WaColors.TextPrimary,
                        )
                        Text(
                            text = "Scan from WhatsApp -> Linked devices -> Link a device",
                            style = MaterialTheme.typography.bodyMedium,
                            color = WaColors.TextSecondary,
                        )
                    }

                    val qrBitmap by produceState<Bitmap?>(initialValue = null, state.qrCode) {
                        value = state.qrCode?.let { generateQrBitmap(it) }
                    }

                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (qrBitmap != null) {
                            Box(
                                modifier = Modifier
                                    .size(224.dp)
                                    .clip(RoundedCornerShape(WaShapes.InputRadius))
                                    .background(androidx.compose.ui.graphics.Color.White)
                                    .padding(12.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                Image(
                                    bitmap = qrBitmap!!.asImageBitmap(),
                                    contentDescription = "WhatsApp QR code",
                                    modifier = Modifier.fillMaxSize(),
                                )
                            }
                        } else {
                            Box(
                                modifier = Modifier
                                    .size(224.dp)
                                    .clip(RoundedCornerShape(WaShapes.InputRadius))
                                    .background(WaColors.SurfaceVariant)
                                    .border(WaShapes.BorderWidth, WaColors.CardBorder, RoundedCornerShape(WaShapes.InputRadius)),
                                contentAlignment = Alignment.Center,
                            ) {
                                Column(
                                    horizontalAlignment = Alignment.CenterHorizontally,
                                    verticalArrangement = Arrangement.spacedBy(8.dp),
                                ) {
                                    Icon(
                                        Icons.Rounded.QrCode2,
                                        contentDescription = null,
                                        tint = WaColors.TextMuted,
                                        modifier = Modifier.size(32.dp),
                                    )
                                    Text(
                                        text = if (state.phase == ConnectionPhase.CONNECTING) {
                                            "Generating QR"
                                        } else {
                                            "Connect to generate QR"
                                        },
                                        style = MaterialTheme.typography.bodyMedium,
                                        color = WaColors.TextSecondary,
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SyncAndGhostPanel(
    state: WaUiState,
    onUpdateGhost: ((GhostConfig) -> GhostConfig) -> Unit,
    onResyncContacts: () -> Unit,
) {
    val syncLabel = when (state.syncState) {
        SyncState.STOPPED -> "Idle"
        SyncState.CONNECTING -> "Connecting"
        SyncState.WS_CONNECTED -> "Socket connected"
        SyncState.SYNCING_OFFLINE -> "Syncing history"
        SyncState.READY -> "Synchronized"
    }

    Card(
        colors = CardDefaults.cardColors(containerColor = WaColors.CardBackground),
        border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
        shape = RoundedCornerShape(WaShapes.CardRadius),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(
                        text = "Session store & sync",
                        style = MaterialTheme.typography.titleMedium,
                        color = WaColors.TextPrimary,
                    )
                    Text(
                        text = syncLabel,
                        style = MaterialTheme.typography.labelMedium,
                        color = if (state.syncState == SyncState.READY) WaColors.Primary else WaColors.TextSecondary,
                    )
                }
                if (state.syncState == SyncState.READY) {
                    OutlinedButton(
                        onClick = onResyncContacts,
                        modifier = Modifier.height(36.dp),
                        border = BorderStroke(WaShapes.BorderWidth, WaColors.Border),
                        shape = RoundedCornerShape(WaShapes.InputRadius),
                    ) {
                        Icon(
                            Icons.Rounded.Sync,
                            contentDescription = null,
                            tint = WaColors.TextSecondary,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("Resync", color = WaColors.TextPrimary)
                    }
                }
            }

            Surface(
                color = WaColors.SurfaceVariant,
                shape = RoundedCornerShape(WaShapes.InputRadius),
                border = BorderStroke(WaShapes.BorderWidth, WaColors.Divider),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(IntrinsicSize.Min)
                        .padding(vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TelemetryCell(label = "Chats", value = state.chatCount, highlight = true, modifier = Modifier.weight(1f))
                    VerticalDivider(modifier = Modifier.fillMaxHeight(), color = WaColors.Divider)
                    TelemetryCell(label = "Messages", value = state.messageCount, highlight = false, modifier = Modifier.weight(1f))
                    VerticalDivider(modifier = Modifier.fillMaxHeight(), color = WaColors.Divider)
                    TelemetryCell(label = "Contacts", value = state.contactCount, highlight = false, modifier = Modifier.weight(1f))
                }
            }

            HorizontalDivider(color = WaColors.Divider)

            Text(
                text = "Privacy controls",
                style = MaterialTheme.typography.titleMedium,
                color = WaColors.TextPrimary,
            )
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                GhostToggleRow(
                    title = "Ghost read",
                    subtitle = "Hide blue read receipts",
                    checked = state.ghostConfig.ghostRead,
                    onCheckedChange = { v -> onUpdateGhost { it.copy(ghostRead = v) } },
                )
                GhostToggleRow(
                    title = "Ghost presence",
                    subtitle = "Appear offline while active",
                    checked = state.ghostConfig.ghostPresence,
                    onCheckedChange = { v -> onUpdateGhost { it.copy(ghostPresence = v) } },
                )
                GhostToggleRow(
                    title = "Ghost typing",
                    subtitle = "Hide typing and recording status",
                    checked = state.ghostConfig.ghostTyping,
                    onCheckedChange = { v -> onUpdateGhost { it.copy(ghostTyping = v) } },
                )
                GhostToggleRow(
                    title = "Ghost view-once",
                    subtitle = "Save view-once media locally",
                    checked = state.ghostConfig.ghostViewOnceInline,
                    onCheckedChange = { v -> onUpdateGhost { it.copy(ghostViewOnceInline = v) } },
                )
            }
        }
    }
}

@Composable
private fun TelemetryCell(
    label: String,
    value: Int,
    highlight: Boolean,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.padding(horizontal = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(
            text = value.toString(),
            style = TextStyle(
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Medium,
                fontSize = 18.sp,
                fontFeatureSettings = "tnum",
            ),
            color = if (highlight) WaColors.Primary else WaColors.TextPrimary,
        )
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            color = WaColors.TextSecondary,
        )
    }
}

@Composable
private fun GhostToggleRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(
            modifier = Modifier
                .weight(1f)
                .padding(end = 12.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                color = WaColors.TextPrimary,
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodyMedium,
                color = WaColors.TextSecondary,
            )
        }
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = WaColors.ButtonText,
                checkedTrackColor = WaColors.Primary,
                uncheckedThumbColor = WaColors.TextSecondary,
                uncheckedTrackColor = WaColors.SurfaceElevated,
                uncheckedBorderColor = WaColors.Border,
            ),
        )
    }
}

@Composable
private fun EngineLogPanel(logs: List<String>) {
    Card(
        colors = CardDefaults.cardColors(containerColor = WaColors.CardBackground),
        border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
        shape = RoundedCornerShape(WaShapes.CardRadius),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "Engine log",
                style = MaterialTheme.typography.titleMedium,
                color = WaColors.TextPrimary,
            )
            if (logs.isEmpty()) {
                Text(
                    text = "No events recorded yet",
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.TextMuted,
                )
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    logs.takeLast(12).forEach { line ->
                        Text(
                            text = line,
                            style = MaterialTheme.typography.labelMedium,
                            color = WaColors.TextSecondary,
                        )
                    }
                }
            }
        }
    }
}

private suspend fun generateQrBitmap(content: String, sizePx: Int = 512): Bitmap? =
    withContext(Dispatchers.Default) {
        runCatching {
            val matrix = QRCodeWriter().encode(content, BarcodeFormat.QR_CODE, sizePx, sizePx)
            val bmp = Bitmap.createBitmap(sizePx, sizePx, Bitmap.Config.ARGB_8888)
            for (y in 0 until sizePx) {
                for (x in 0 until sizePx) {
                    bmp.setPixel(x, y, if (matrix[x, y]) Color.BLACK else Color.WHITE)
                }
            }
            bmp
        }.getOrNull()
    }
