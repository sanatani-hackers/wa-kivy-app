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
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.TabRowDefaults.SecondaryIndicator
import androidx.compose.material3.TabRowDefaults.tabIndicatorOffset
import androidx.compose.material3.Text
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
import kt.sanatani.wapower.core.WaUiState
import kt.sanatani.wapower.ui.theme.WaColors

@Composable
fun ConnectScreen(
    state: WaUiState,
    onStartConnect: (forceNew: Boolean) -> Unit,
    onStopConnect: () -> Unit,
    onRequestPairCode: (String) -> Unit,
    onLogoutReset: () -> Unit,
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
                Card(
                    colors = CardDefaults.cardColors(containerColor = WaColors.Surface),
                    border = BorderStroke(1.dp, WaColors.Stroke),
                    shape = RoundedCornerShape(16.dp),
                ) {
                    Column(modifier = Modifier.fillMaxWidth()) {
                        TabRow(
                            selectedTabIndex = selectedTab,
                            containerColor = WaColors.Surface,
                            contentColor = WaColors.Emerald,
                            indicator = { tabPositions ->
                                SecondaryIndicator(
                                    modifier = Modifier.tabIndicatorOffset(tabPositions[selectedTab]),
                                    color = WaColors.Emerald,
                                )
                            },
                            divider = { HorizontalDivider(color = WaColors.Stroke) },
                        ) {
                            Tab(
                                selected = selectedTab == 0,
                                onClick = { selectedTab = 0 },
                                text = { Text("Phone Code") },
                                icon = { Icon(Icons.Rounded.PhoneAndroid, contentDescription = null) },
                            )
                            Tab(
                                selected = selectedTab == 1,
                                onClick = { selectedTab = 1 },
                                text = { Text("QR Link") },
                                icon = { Icon(Icons.Rounded.QrCode2, contentDescription = null) },
                            )
                        }

                        Column(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(12.dp),
                        ) {
                            if (selectedTab == 0) {
                                Text(
                                    text = "Link with 8-Digit Pairing Code",
                                    style = MaterialTheme.typography.titleMedium,
                                    color = WaColors.TextPrimary,
                                )
                                Text(
                                    text = "Include country code without '+' (for example 919876543210).",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = WaColors.TextSecondary,
                                )
                                OutlinedTextField(
                                    value = phoneInput,
                                    onValueChange = { phoneInput = it },
                                    modifier = Modifier.fillMaxWidth(),
                                    singleLine = true,
                                    label = { Text("WhatsApp Number") },
                                    placeholder = { Text("919876543210") },
                                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                                    colors = OutlinedTextFieldDefaults.colors(
                                        focusedBorderColor = WaColors.Emerald,
                                        unfocusedBorderColor = WaColors.Stroke,
                                        focusedLabelColor = WaColors.Emerald,
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
                                        .height(48.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = WaColors.Emerald,
                                        contentColor = WaColors.Background,
                                    ),
                                    shape = RoundedCornerShape(12.dp),
                                ) {
                                    if (state.pairingBusy) {
                                        CircularProgressIndicator(
                                            modifier = Modifier.size(18.dp),
                                            strokeWidth = 2.dp,
                                            color = WaColors.Background,
                                        )
                                        Spacer(Modifier.width(8.dp))
                                        Text("Requesting Code...")
                                    } else {
                                        Text("Get Pairing Code", fontWeight = FontWeight.SemiBold)
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
                                        color = WaColors.SurfaceElevated,
                                        shape = RoundedCornerShape(12.dp),
                                        border = BorderStroke(1.dp, WaColors.Emerald.copy(alpha = 0.5f)),
                                        modifier = Modifier.fillMaxWidth(),
                                    ) {
                                        Row(
                                            modifier = Modifier
                                                .fillMaxWidth()
                                                .padding(horizontal = 16.dp, vertical = 12.dp),
                                            verticalAlignment = Alignment.CenterVertically,
                                            horizontalArrangement = Arrangement.SpaceBetween,
                                        ) {
                                            Column {
                                                Text(
                                                    text = "Enter on Phone -> Linked Devices",
                                                    style = MaterialTheme.typography.bodyMedium,
                                                    color = WaColors.TextSecondary,
                                                )
                                                Spacer(Modifier.height(4.dp))
                                                Text(
                                                    text = state.pairingCode,
                                                    fontFamily = FontFamily.Monospace,
                                                    fontWeight = FontWeight.Bold,
                                                    fontSize = 26.sp,
                                                    letterSpacing = 3.sp,
                                                    color = WaColors.Emerald,
                                                )
                                            }
                                            IconButton(
                                                onClick = {
                                                    clipboard.setText(AnnotatedString(state.pairingCode))
                                                }
                                            ) {
                                                Icon(
                                                    Icons.Rounded.ContentCopy,
                                                    contentDescription = "Copy pairing code",
                                                    tint = WaColors.TextPrimary,
                                                )
                                            }
                                        }
                                    }
                                }
                            } else {
                                Text(
                                    text = "Link with QR Code",
                                    style = MaterialTheme.typography.titleMedium,
                                    color = WaColors.TextPrimary,
                                )
                                Text(
                                    text = "Start the engine below and scan from WhatsApp -> Linked Devices -> Link a Device.",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = WaColors.TextSecondary,
                                )

                                val qrBitmap by produceState<Bitmap?>(initialValue = null, state.qrCode) {
                                    value = state.qrCode?.let { generateQrBitmap(it) }
                                }

                                Box(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(vertical = 8.dp),
                                    contentAlignment = Alignment.Center,
                                ) {
                                    if (qrBitmap != null) {
                                        Box(
                                            modifier = Modifier
                                                .size(236.dp)
                                                .clip(RoundedCornerShape(16.dp))
                                                .background(androidx.compose.ui.graphics.Color.White)
                                                .padding(12.dp),
                                            contentAlignment = Alignment.Center,
                                        ) {
                                            Image(
                                                bitmap = qrBitmap!!.asImageBitmap(),
                                                contentDescription = "WhatsApp QR Code",
                                                modifier = Modifier.fillMaxSize(),
                                            )
                                        }
                                    } else {
                                        Box(
                                            modifier = Modifier
                                                .size(236.dp)
                                                .clip(RoundedCornerShape(16.dp))
                                                .background(WaColors.SurfaceElevated)
                                                .border(1.dp, WaColors.Stroke, RoundedCornerShape(16.dp)),
                                            contentAlignment = Alignment.Center,
                                        ) {
                                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                                Icon(
                                                    Icons.Rounded.QrCode2,
                                                    contentDescription = null,
                                                    tint = WaColors.TextMuted,
                                                    modifier = Modifier.size(48.dp),
                                                )
                                                Spacer(Modifier.height(8.dp))
                                                Text(
                                                    text = if (state.phase == ConnectionPhase.CONNECTING) {
                                                        "Waiting for QR..."
                                                    } else {
                                                        "Tap Connect to generate QR"
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

            NativeLogPanel(logs = state.logs)
        }
    }
}

@Composable
private fun HeaderBanner(state: WaUiState) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(
                modifier = Modifier
                    .size(44.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(WaColors.Emerald.copy(alpha = 0.14f))
                    .border(1.dp, WaColors.Emerald.copy(alpha = 0.35f), RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = Icons.Rounded.Bolt,
                    contentDescription = null,
                    tint = WaColors.Emerald,
                )
            }
            Column {
                Text(
                    text = "WA power",
                    style = MaterialTheme.typography.headlineMedium,
                    color = WaColors.TextPrimary,
                )
                Text(
                    text = "Native Go JNI Core ${state.nativeVersion.ifBlank { "..." }}",
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.TextSecondary,
                )
            }
        }
        PhaseBadge(phase = state.phase)
    }
}

@Composable
private fun PhaseBadge(phase: ConnectionPhase) {
    val (label, color) = when (phase) {
        ConnectionPhase.STOPPED -> "IDLE" to WaColors.TextMuted
        ConnectionPhase.CONNECTING -> "CONNECTING" to WaColors.AmberWarn
        ConnectionPhase.AWAITING_PAIRING -> "PAIRING" to WaColors.SkyInfo
        ConnectionPhase.CONNECTED -> "CONNECTED" to WaColors.Emerald
        ConnectionPhase.READY -> "READY" to WaColors.Emerald
        ConnectionPhase.LOGGED_OUT -> "LOGGED OUT" to WaColors.Danger
        ConnectionPhase.ERROR -> "ERROR" to WaColors.Danger
    }
    Surface(
        color = color.copy(alpha = 0.14f),
        border = BorderStroke(1.dp, color.copy(alpha = 0.4f)),
        shape = CircleShape,
    ) {
        Text(
            text = label,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelMedium,
            color = color,
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
        colors = CardDefaults.cardColors(containerColor = WaColors.Surface),
        border = BorderStroke(1.dp, WaColors.Stroke),
        shape = RoundedCornerShape(16.dp),
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
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = if (state.selfJid != null) {
                            state.pushName?.takeIf { it.isNotBlank() } ?: state.selfJid
                        } else if (state.storedDevices.isNotEmpty()) {
                            "Saved session: ${state.storedDevices.first().jid}"
                        } else {
                            "No WhatsApp device linked"
                        },
                        style = MaterialTheme.typography.titleMedium,
                        color = WaColors.TextPrimary,
                    )
                    if (state.selfJid != null) {
                        Text(
                            text = state.selfJid,
                            style = MaterialTheme.typography.labelMedium,
                            color = WaColors.SkyInfo,
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
                        tint = WaColors.Emerald,
                        modifier = Modifier.size(28.dp),
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                if (!isRunning) {
                    Button(
                        onClick = { onStartConnect(false) },
                        modifier = Modifier.weight(1f),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = WaColors.Emerald,
                            contentColor = WaColors.Background,
                        ),
                        shape = RoundedCornerShape(12.dp),
                    ) {
                        Icon(Icons.Rounded.PlayArrow, contentDescription = null)
                        Spacer(Modifier.width(6.dp))
                        Text(
                            if (state.storedDevices.isNotEmpty()) "Reconnect" else "Connect",
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                } else {
                    OutlinedButton(
                        onClick = onStopConnect,
                        modifier = Modifier.weight(1f),
                        border = BorderStroke(1.dp, WaColors.Stroke),
                        shape = RoundedCornerShape(12.dp),
                    ) {
                        Icon(Icons.Rounded.Stop, contentDescription = null, tint = WaColors.TextPrimary)
                        Spacer(Modifier.width(6.dp))
                        Text("Stop", color = WaColors.TextPrimary)
                    }
                }

                if (state.storedDevices.isNotEmpty() || state.selfJid != null) {
                    OutlinedButton(
                        onClick = onLogoutReset,
                        border = BorderStroke(1.dp, WaColors.Danger.copy(alpha = 0.5f)),
                        shape = RoundedCornerShape(12.dp),
                    ) {
                        Icon(Icons.Rounded.LinkOff, contentDescription = null, tint = WaColors.Danger)
                        Spacer(Modifier.width(6.dp))
                        Text("Unlink", color = WaColors.Danger)
                    }
                }
            }
        }
    }
}

@Composable
private fun NativeLogPanel(logs: List<String>) {
    Card(
        colors = CardDefaults.cardColors(containerColor = WaColors.Surface),
        border = BorderStroke(1.dp, WaColors.Stroke),
        shape = RoundedCornerShape(16.dp),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "JNI Proof-of-Life Log",
                style = MaterialTheme.typography.titleMedium,
                color = WaColors.TextPrimary,
            )
            if (logs.isEmpty()) {
                Text(
                    text = "Waiting for native events...",
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.TextMuted,
                )
            } else {
                logs.takeLast(14).forEach { line ->
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
