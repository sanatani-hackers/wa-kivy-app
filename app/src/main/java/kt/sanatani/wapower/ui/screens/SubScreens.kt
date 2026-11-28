package kt.sanatani.wapower.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Build
import androidx.compose.material.icons.rounded.Call
import androidx.compose.material.icons.rounded.LinkOff
import androidx.compose.material.icons.rounded.SettingsEthernet
import androidx.compose.material.icons.rounded.Sync
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kt.sanatani.wapower.core.GhostConfig
import kt.sanatani.wapower.core.WaUiState
import kt.sanatani.wapower.ui.theme.WaColors
import kt.sanatani.wapower.ui.theme.WaShapes

@Composable
fun CallsPlaceholderScreen(
    activeCallsJson: String,
    onRefreshCalls: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(WaColors.Background)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = "Calls",
            style = MaterialTheme.typography.headlineMedium,
            color = WaColors.TextPrimary,
        )
        Card(
            colors = CardDefaults.cardColors(containerColor = WaColors.CardBackground),
            border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
            shape = RoundedCornerShape(WaShapes.CardRadius),
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Icon(Icons.Rounded.Call, contentDescription = null, tint = WaColors.Primary, modifier = Modifier.size(18.dp))
                    Text("VoIP & YouTube stream deck", style = MaterialTheme.typography.titleMedium, color = WaColors.TextPrimary)
                }
                Text(
                    text = "Active engine state: ${activeCallsJson.ifBlank { "Idle" }}",
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.TextSecondary,
                )
                Text(
                    text = "Place 1-tap voice or video calls from Contacts; full live YouTube AV/PCM DJ deck arrives in Sprint 4",
                    style = MaterialTheme.typography.bodyMedium,
                    color = WaColors.TextSecondary,
                )
                OutlinedButton(
                    onClick = onRefreshCalls,
                    border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                    shape = RoundedCornerShape(WaShapes.ButtonRadius),
                ) {
                    Text("Refresh active calls", color = WaColors.TextPrimary)
                }
            }
        }
    }
}

@Composable
fun ToolsPlaceholderScreen() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(WaColors.Background)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = "Tools",
            style = MaterialTheme.typography.headlineMedium,
            color = WaColors.TextPrimary,
        )
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
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Icon(Icons.Rounded.Build, contentDescription = null, tint = WaColors.Primary, modifier = Modifier.size(18.dp))
                    Text("Native power modules", style = MaterialTheme.typography.titleMedium, color = WaColors.TextPrimary)
                }
                Text(
                    text = "Anti-Delete and View-Once preservation are active in background storage; universal AI assistant, Call Guard, Auto-Reply, and session backup controls arrive in Sprint 5",
                    style = MaterialTheme.typography.bodyMedium,
                    color = WaColors.TextSecondary,
                )
            }
        }
    }
}

@Composable
fun SettingsScreen(
    state: WaUiState,
    onUpdateGhost: ((GhostConfig) -> GhostConfig) -> Unit,
    onResyncContacts: () -> Unit,
    onOpenConnectionView: () -> Unit,
    onLogoutReset: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(WaColors.Background)
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = "Settings",
            style = MaterialTheme.typography.headlineMedium,
            color = WaColors.TextPrimary,
        )

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
                Text(
                    text = state.pushName?.takeIf { it.isNotBlank() } ?: (state.selfJid ?: "WA power session"),
                    style = MaterialTheme.typography.titleMedium,
                    color = WaColors.TextPrimary,
                )
                Text(
                    text = "${state.selfJid ?: "Not paired"}  •  ${state.syncState.name}",
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.TextSecondary,
                )
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    OutlinedButton(
                        onClick = onOpenConnectionView,
                        modifier = Modifier.weight(1f),
                        border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                    ) {
                        Icon(Icons.Rounded.SettingsEthernet, contentDescription = null, tint = WaColors.Primary, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Connection", color = WaColors.TextPrimary)
                    }
                    OutlinedButton(
                        onClick = onResyncContacts,
                        modifier = Modifier.weight(1f),
                        border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                    ) {
                        Icon(Icons.Rounded.Sync, contentDescription = null, tint = WaColors.TextSecondary, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Resync", color = WaColors.TextPrimary)
                    }
                }
                OutlinedButton(
                    onClick = onLogoutReset,
                    modifier = Modifier.fillMaxWidth(),
                    border = BorderStroke(WaShapes.BorderWidth, WaColors.Danger.copy(alpha = 0.45f)),
                    shape = RoundedCornerShape(WaShapes.ButtonRadius),
                ) {
                    Icon(Icons.Rounded.LinkOff, contentDescription = null, tint = WaColors.Danger, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Unlink session", color = WaColors.Danger)
                }
            }
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
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text("Privacy controls", style = MaterialTheme.typography.titleMedium, color = WaColors.TextPrimary)
                HorizontalDivider(color = WaColors.Divider)
                SettingsSwitch("Ghost read", "Hide blue read receipts", state.ghostConfig.ghostRead) { v ->
                    onUpdateGhost { it.copy(ghostRead = v) }
                }
                SettingsSwitch("Ghost presence", "Appear offline while active", state.ghostConfig.ghostPresence) { v ->
                    onUpdateGhost { it.copy(ghostPresence = v) }
                }
                SettingsSwitch("Ghost typing", "Hide typing and recording status", state.ghostConfig.ghostTyping) { v ->
                    onUpdateGhost { it.copy(ghostTyping = v) }
                }
                SettingsSwitch("Ghost view-once", "Save view-once media locally", state.ghostConfig.ghostViewOnceInline) { v ->
                    onUpdateGhost { it.copy(ghostViewOnceInline = v) }
                }
            }
        }
    }
}

@Composable
private fun SettingsSwitch(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Column(modifier = Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = WaColors.TextPrimary)
            Text(subtitle, style = MaterialTheme.typography.bodyMedium, color = WaColors.TextSecondary)
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
