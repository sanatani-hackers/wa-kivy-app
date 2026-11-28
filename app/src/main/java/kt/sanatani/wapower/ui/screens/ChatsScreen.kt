package kt.sanatani.wapower.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Archive
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.DoneAll
import androidx.compose.material.icons.rounded.PushPin
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.Unarchive
import androidx.compose.material.icons.rounded.VolumeOff
import androidx.compose.material.icons.rounded.VolumeUp
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kt.sanatani.wapower.core.SyncState
import kt.sanatani.wapower.db.ChatListRow
import kt.sanatani.wapower.ui.components.WaAvatar
import kt.sanatani.wapower.ui.components.formatShortTime
import kt.sanatani.wapower.ui.theme.WaColors
import kt.sanatani.wapower.ui.theme.WaShapes

enum class ChatFilter(val label: String) {
    ALL("All"),
    UNREAD("Unread"),
    GROUPS("Groups"),
    ARCHIVED("Archived"),
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun ChatsScreen(
    activeChats: List<ChatListRow>,
    archivedChats: List<ChatListRow>,
    syncState: SyncState,
    onOpenChat: (String) -> Unit,
    onTogglePin: (String, Boolean) -> Unit,
    onToggleArchive: (String, Boolean) -> Unit,
    onToggleMute: (String, Boolean) -> Unit,
    onMarkRead: (String) -> Unit,
    onRequestAvatar: (String) -> Unit,
) {
    var filter by rememberSaveable { mutableStateOf(ChatFilter.ALL) }
    var query by rememberSaveable { mutableStateOf("") }
    var sheetChat by remember { mutableStateOf<ChatListRow?>(null) }

    val filtered = remember(activeChats, archivedChats, filter, query) {
        val base = when (filter) {
            ChatFilter.ALL -> activeChats
            ChatFilter.UNREAD -> activeChats.filter { it.chat.unreadCount > 0 }
            ChatFilter.GROUPS -> activeChats.filter { it.chat.isGroup }
            ChatFilter.ARCHIVED -> archivedChats
        }
        val q = query.trim().lowercase()
        if (q.isEmpty()) {
            base
        } else {
            base.filter {
                it.displayName.lowercase().contains(q) ||
                    it.chat.jid.lowercase().contains(q) ||
                    it.chat.lastPreview.lowercase().contains(q)
            }
        }
    }

    val totalUnread = remember(activeChats) { activeChats.sumOf { it.chat.unreadCount } }

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
                    text = "Chats",
                    style = MaterialTheme.typography.headlineMedium,
                    color = WaColors.TextPrimary,
                )
                Text(
                    text = when {
                        totalUnread > 0 -> "$totalUnread unread"
                        syncState == SyncState.READY -> "${activeChats.size} conversations"
                        else -> "Syncing ${syncState.name.lowercase().replace('_', ' ')}"
                    },
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.TextSecondary,
                )
            }
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .clip(CircleShape)
                    .background(if (syncState == SyncState.READY) WaColors.Primary else WaColors.Warning),
            )
        }

        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            singleLine = true,
            placeholder = { Text("Search", color = WaColors.TextMuted) },
            leadingIcon = {
                Icon(
                    Icons.Rounded.Search,
                    contentDescription = null,
                    tint = WaColors.TextSecondary,
                    modifier = Modifier.size(18.dp),
                )
            },
            trailingIcon = {
                if (query.isNotEmpty()) {
                    IconButton(onClick = { query = "" }) {
                        Icon(
                            Icons.Rounded.Close,
                            contentDescription = "Clear",
                            tint = WaColors.TextSecondary,
                            modifier = Modifier.size(18.dp),
                        )
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
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(ChatFilter.entries, key = { it.name }) { item ->
                val selected = filter == item
                FilterChip(
                    selected = selected,
                    onClick = { filter = item },
                    label = {
                        Text(
                            text = item.label,
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    },
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
                modifier = Modifier
                    .fillMaxSize()
                    .padding(24.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = if (query.isNotBlank()) "No matching chats" else "No conversations synced yet",
                    style = MaterialTheme.typography.bodyLarge,
                    color = WaColors.TextMuted,
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
            ) {
                items(
                    items = filtered,
                    key = { it.chat.jid },
                ) { row ->
                    if (row.resolvedAvatar.isBlank()) {
                        LaunchedEffect(row.chat.jid) {
                            onRequestAvatar(row.chat.jid)
                        }
                    }

                    ChatListItemRow(
                        row = row,
                        onClick = { onOpenChat(row.chat.jid) },
                        onLongClick = { sheetChat = row },
                    )
                    HorizontalDivider(
                        color = WaColors.Divider,
                        modifier = Modifier.padding(start = 76.dp),
                    )
                }
            }
        }
    }

    sheetChat?.let { target ->
        val isMuted = target.chat.mutedUntil > (System.currentTimeMillis() / 1000L)
        ModalBottomSheet(
            onDismissRequest = { sheetChat = null },
            containerColor = WaColors.Surface,
            contentColor = WaColors.TextPrimary,
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    text = target.displayName,
                    style = MaterialTheme.typography.titleMedium,
                    color = WaColors.TextPrimary,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
                )
                HorizontalDivider(color = WaColors.Divider)

                SheetActionRow(
                    icon = Icons.Rounded.PushPin,
                    label = if (target.chat.pinned) "Unpin chat" else "Pin chat",
                    onClick = {
                        onTogglePin(target.chat.jid, !target.chat.pinned)
                        sheetChat = null
                    },
                )
                SheetActionRow(
                    icon = if (target.chat.archived) Icons.Rounded.Unarchive else Icons.Rounded.Archive,
                    label = if (target.chat.archived) "Unarchive chat" else "Archive chat",
                    onClick = {
                        onToggleArchive(target.chat.jid, !target.chat.archived)
                        sheetChat = null
                    },
                )
                SheetActionRow(
                    icon = if (isMuted) Icons.Rounded.VolumeUp else Icons.Rounded.VolumeOff,
                    label = if (isMuted) "Unmute notifications" else "Mute for 8 hours",
                    onClick = {
                        onToggleMute(target.chat.jid, !isMuted)
                        sheetChat = null
                    },
                )
                if (target.chat.unreadCount > 0) {
                    SheetActionRow(
                        icon = Icons.Rounded.DoneAll,
                        label = "Mark read",
                        onClick = {
                            onMarkRead(target.chat.jid)
                            sheetChat = null
                        },
                    )
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ChatListItemRow(
    row: ChatListRow,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
) {
    val isMuted = row.chat.mutedUntil > (System.currentTimeMillis() / 1000L)
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .combinedClickable(
                onClick = onClick,
                onLongClick = onLongClick,
            )
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        WaAvatar(
            name = row.displayName,
            avatarPath = row.resolvedAvatar,
            isOnline = row.isOnline,
            size = 48.dp,
        )

        Column(
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    text = row.displayName,
                    style = MaterialTheme.typography.titleMedium,
                    color = WaColors.TextPrimary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f).padding(end = 8.dp),
                )
                Text(
                    text = formatShortTime(row.chat.lastTs),
                    style = MaterialTheme.typography.labelMedium,
                    color = if (row.chat.unreadCount > 0) WaColors.Primary else WaColors.TextMuted,
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    text = if (row.isTyping) "Typing..." else row.chat.lastPreview.ifBlank { row.chat.jid },
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (row.isTyping) WaColors.Primary else WaColors.TextSecondary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f).padding(end = 8.dp),
                )
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (isMuted) {
                        Icon(
                            Icons.Rounded.VolumeOff,
                            contentDescription = "Muted",
                            tint = WaColors.TextMuted,
                            modifier = Modifier.size(14.dp),
                        )
                    }
                    if (row.chat.pinned) {
                        Icon(
                            Icons.Rounded.PushPin,
                            contentDescription = "Pinned",
                            tint = WaColors.TextMuted,
                            modifier = Modifier.size(14.dp),
                        )
                    }
                    if (row.chat.unreadCount > 0) {
                        Surface(
                            color = WaColors.Primary,
                            shape = CircleShape,
                        ) {
                            Text(
                                text = row.chat.unreadCount.toString(),
                                style = TextStyle(
                                    fontFamily = FontFamily.Monospace,
                                    fontWeight = FontWeight.Medium,
                                    fontSize = 11.sp,
                                    fontFeatureSettings = "tnum",
                                ),
                                color = WaColors.ButtonText,
                                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SheetActionRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    onClick: () -> Unit,
) {
    TextButton(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = WaColors.TextSecondary,
                modifier = Modifier.size(18.dp),
            )
            Text(
                text = label,
                style = MaterialTheme.typography.bodyLarge,
                color = WaColors.TextPrimary,
            )
        }
    }
}
