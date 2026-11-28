package kt.sanatani.wapower.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Call
import androidx.compose.material.icons.rounded.ChatBubbleOutline
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.PersonSearch
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Sync
import androidx.compose.material.icons.rounded.Videocam
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import kt.sanatani.wapower.core.WhatsAppCheckResult
import kt.sanatani.wapower.db.ContactEntity
import kt.sanatani.wapower.ui.components.WaAvatar
import kt.sanatani.wapower.ui.theme.WaColors
import kt.sanatani.wapower.ui.theme.WaShapes

private enum class ContactFilter(val label: String) {
    ALL("All"),
    PEOPLE("People"),
    GROUPS("Groups"),
}

@Composable
fun ContactsScreen(
    contacts: List<ContactEntity>,
    onResync: () -> Unit,
    onCheckOnWhatsApp: suspend (String) -> WhatsAppCheckResult,
    onOpenChat: (String) -> Unit,
    onPlaceCall: (String, Boolean) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var filter by rememberSaveable { mutableStateOf(ContactFilter.ALL) }
    var query by rememberSaveable { mutableStateOf("") }
    var showChecker by rememberSaveable { mutableStateOf(false) }
    var checkPhone by rememberSaveable { mutableStateOf("") }
    var checking by remember { mutableStateOf(false) }
    var checkResult by remember { mutableStateOf<WhatsAppCheckResult?>(null) }

    val filtered = remember(contacts, filter, query) {
        val base = when (filter) {
            ContactFilter.ALL -> contacts
            ContactFilter.PEOPLE -> contacts.filter { !it.jid.endsWith("@g.us") }
            ContactFilter.GROUPS -> contacts.filter { it.jid.endsWith("@g.us") }
        }
        val q = query.trim().lowercase()
        if (q.isEmpty()) {
            base
        } else {
            base.filter {
                it.fullName.lowercase().contains(q) ||
                    it.pushName.lowercase().contains(q) ||
                    it.phone.lowercase().contains(q) ||
                    it.jid.lowercase().contains(q)
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(WaColors.Background),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    text = "Contacts",
                    style = MaterialTheme.typography.headlineMedium,
                    color = WaColors.TextPrimary,
                )
                Text(
                    text = "${contacts.size} synced",
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.TextSecondary,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = { showChecker = !showChecker },
                    modifier = Modifier.height(36.dp),
                    border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                    shape = RoundedCornerShape(WaShapes.InputRadius),
                ) {
                    Icon(
                        Icons.Rounded.PersonSearch,
                        contentDescription = null,
                        tint = WaColors.Primary,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text("Lookup", color = WaColors.TextPrimary)
                }
                OutlinedButton(
                    onClick = onResync,
                    modifier = Modifier.height(36.dp),
                    border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                    shape = RoundedCornerShape(WaShapes.InputRadius),
                ) {
                    Icon(
                        Icons.Rounded.Sync,
                        contentDescription = null,
                        tint = WaColors.TextSecondary,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(Modifier.width(6.dp))
                    Text("Resync", color = WaColors.TextPrimary)
                }
            }
        }

        if (showChecker) {
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
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
                    Text(
                        text = "Verify WhatsApp number",
                        style = MaterialTheme.typography.titleMedium,
                        color = WaColors.TextPrimary,
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        OutlinedTextField(
                            value = checkPhone,
                            onValueChange = { checkPhone = it },
                            modifier = Modifier.weight(1f),
                            singleLine = true,
                            placeholder = { Text("919876543210", color = WaColors.TextMuted) },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                            shape = RoundedCornerShape(WaShapes.InputRadius),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedContainerColor = WaColors.InputBackground,
                                unfocusedContainerColor = WaColors.InputBackground,
                                focusedBorderColor = WaColors.Primary,
                                unfocusedBorderColor = WaColors.InputBorder,
                                focusedTextColor = WaColors.TextPrimary,
                                unfocusedTextColor = WaColors.TextPrimary,
                            ),
                        )
                        Button(
                            onClick = {
                                scope.launch {
                                    checking = true
                                    checkResult = onCheckOnWhatsApp(checkPhone)
                                    checking = false
                                }
                            },
                            enabled = !checking,
                            modifier = Modifier.height(48.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = WaColors.ButtonBackground,
                                contentColor = WaColors.ButtonText,
                            ),
                            shape = RoundedCornerShape(WaShapes.ButtonRadius),
                        ) {
                            if (checking) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(16.dp),
                                    strokeWidth = 2.dp,
                                    color = WaColors.ButtonText,
                                )
                            } else {
                                Text("Check", fontWeight = FontWeight.Medium)
                            }
                        }
                    }

                    checkResult?.let { res ->
                        if (!res.ok) {
                            Text(res.error, style = MaterialTheme.typography.bodyMedium, color = WaColors.Danger)
                        } else if (res.registered && res.jid.isNotBlank()) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween,
                            ) {
                                Text(
                                    text = "Registered: ${res.jid}",
                                    style = MaterialTheme.typography.labelMedium,
                                    color = WaColors.Primary,
                                    modifier = Modifier.weight(1f),
                                )
                                Row {
                                    IconButton(onClick = { onOpenChat(res.jid) }) {
                                        Icon(Icons.Rounded.ChatBubbleOutline, contentDescription = "Chat", tint = WaColors.Primary, modifier = Modifier.size(18.dp))
                                    }
                                    IconButton(onClick = { onPlaceCall(res.jid, false) }) {
                                        Icon(Icons.Rounded.Call, contentDescription = "Voice call", tint = WaColors.TextSecondary, modifier = Modifier.size(18.dp))
                                    }
                                    IconButton(onClick = { onPlaceCall(res.jid, true) }) {
                                        Icon(Icons.Rounded.Videocam, contentDescription = "Video call", tint = WaColors.TextSecondary, modifier = Modifier.size(18.dp))
                                    }
                                }
                            }
                        } else {
                            Text(
                                text = "Number is not on WhatsApp",
                                style = MaterialTheme.typography.bodyMedium,
                                color = WaColors.TextSecondary,
                            )
                        }
                    }
                }
            }
        }

        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp),
            singleLine = true,
            placeholder = { Text("Search", color = WaColors.TextMuted) },
            leadingIcon = {
                Icon(Icons.Rounded.Search, contentDescription = null, tint = WaColors.TextSecondary, modifier = Modifier.size(18.dp))
            },
            trailingIcon = {
                if (query.isNotEmpty()) {
                    IconButton(onClick = { query = "" }) {
                        Icon(Icons.Rounded.Close, contentDescription = "Clear", tint = WaColors.TextSecondary, modifier = Modifier.size(18.dp))
                    }
                }
            },
            shape = RoundedCornerShape(WaShapes.InputRadius),
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = WaColors.InputBackground,
                unfocusedContainerColor = WaColors.InputBackground,
                focusedBorderColor = WaColors.Primary,
                unfocusedBorderColor = WaColors.CardBorder,
                focusedTextColor = WaColors.TextPrimary,
                unfocusedTextColor = WaColors.TextPrimary,
            ),
        )

        LazyRow(
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(ContactFilter.entries, key = { it.name }) { item ->
                val selected = filter == item
                FilterChip(
                    selected = selected,
                    onClick = { filter = item },
                    label = { Text(item.label, style = MaterialTheme.typography.bodyMedium) },
                    shape = RoundedCornerShape(WaShapes.InputRadius),
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = WaColors.SurfaceElevated,
                        selectedLabelColor = WaColors.Primary,
                        containerColor = WaColors.Surface,
                        labelColor = WaColors.TextSecondary,
                    ),
                    border = FilterChipDefaults.filterChipBorder(
                        enabled = true,
                        selected = selected,
                        borderColor = WaColors.CardBorder,
                        selectedBorderColor = WaColors.Primary.copy(alpha = 0.6f),
                    ),
                )
            }
        }

        HorizontalDivider(color = WaColors.Divider)

        if (filtered.isEmpty()) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = if (query.isNotBlank()) "No matching contacts" else "Tap Resync to load contacts",
                    style = MaterialTheme.typography.bodyLarge,
                    color = WaColors.TextMuted,
                )
            }
        } else {
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(
                    items = filtered,
                    key = { it.jid },
                ) { contact ->
                    val title = contact.fullName.ifBlank { contact.pushName.ifBlank { contact.jid } }
                    val isGroup = contact.jid.endsWith("@g.us")
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        WaAvatar(
                            name = title,
                            avatarPath = contact.avatarPath,
                            isOnline = contact.isOnline,
                            size = 44.dp,
                        )
                        Column(
                            modifier = Modifier.weight(1f),
                            verticalArrangement = Arrangement.spacedBy(2.dp),
                        ) {
                            Text(
                                text = title,
                                style = MaterialTheme.typography.titleMedium,
                                color = WaColors.TextPrimary,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                text = contact.phone.ifBlank { contact.jid },
                                style = MaterialTheme.typography.labelMedium,
                                color = WaColors.TextSecondary,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                        Row {
                            IconButton(onClick = { onOpenChat(contact.jid) }) {
                                Icon(
                                    Icons.Rounded.ChatBubbleOutline,
                                    contentDescription = "Open chat",
                                    tint = WaColors.Primary,
                                    modifier = Modifier.size(18.dp),
                                )
                            }
                            if (!isGroup) {
                                IconButton(onClick = { onPlaceCall(contact.jid, false) }) {
                                    Icon(
                                        Icons.Rounded.Call,
                                        contentDescription = "Voice call",
                                        tint = WaColors.TextSecondary,
                                        modifier = Modifier.size(18.dp),
                                    )
                                }
                                IconButton(onClick = { onPlaceCall(contact.jid, true) }) {
                                    Icon(
                                        Icons.Rounded.Videocam,
                                        contentDescription = "Video call",
                                        tint = WaColors.TextSecondary,
                                        modifier = Modifier.size(18.dp),
                                    )
                                }
                            }
                        }
                    }
                    HorizontalDivider(
                        color = WaColors.Divider,
                        modifier = Modifier.padding(start = 72.dp),
                    )
                }
            }
        }
    }
}
