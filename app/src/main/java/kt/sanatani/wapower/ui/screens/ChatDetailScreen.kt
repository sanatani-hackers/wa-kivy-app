package kt.sanatani.wapower.ui.screens

import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.Reply
import androidx.compose.material.icons.automirrored.rounded.Send
import androidx.compose.material.icons.rounded.AttachFile
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.ContentCopy
import androidx.compose.material.icons.rounded.Description
import androidx.compose.material.icons.rounded.DoneAll
import androidx.compose.material.icons.rounded.Download
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Stop
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import coil.compose.AsyncImage
import kotlinx.coroutines.launch
import kt.sanatani.wapower.core.GhostConfig
import kt.sanatani.wapower.db.ChatListRow
import kt.sanatani.wapower.db.MessageEntity
import kt.sanatani.wapower.ui.components.WaAvatar
import kt.sanatani.wapower.ui.components.formatMessageTime
import kt.sanatani.wapower.ui.components.formatShortTime
import kt.sanatani.wapower.ui.theme.WaColors
import kt.sanatani.wapower.ui.theme.WaShapes
import org.json.JSONObject
import java.io.File
import kotlin.math.roundToInt

private val QUICK_REACTIONS = listOf("👍", "❤️", "😂", "😮", "😢", "🙏")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatDetailScreen(
    chatJid: String,
    chatRow: ChatListRow?,
    messages: List<MessageEntity>,
    ghostConfig: GhostConfig,
    onBack: () -> Unit,
    onSendText: suspend (String, String) -> Unit,
    onSendMedia: suspend (Uri, String, String, String) -> Result<String>,
    onDownloadMedia: suspend (String) -> Result<String>,
    onSendReaction: suspend (String, String, String) -> Unit,
    onMarkReadManual: () -> Unit,
    onMarkReadAuto: () -> Unit,
    onSubscribePresence: () -> Unit,
    onTypingChanged: (Boolean) -> Unit,
) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()

    var inputText by remember { mutableStateOf("") }
    var replyTo by remember { mutableStateOf<MessageEntity?>(null) }
    var selectedMsgForSheet by remember { mutableStateOf<MessageEntity?>(null) }
    var mediaViewerMsg by remember { mutableStateOf<MessageEntity?>(null) }
    var downloadingIds by remember { mutableStateOf<Set<String>>(emptySet()) }
    var uploadingMedia by remember { mutableStateOf(false) }
    var statusNotice by remember { mutableStateOf<String?>(null) }

    val messagesById = remember(messages) { messages.associateBy { it.id } }
    val reversedMessages = remember(messages) { messages.asReversed() }

    LaunchedEffect(chatJid) {
        onSubscribePresence()
        if (!ghostConfig.ghostRead) {
            onMarkReadAuto()
        }
    }

    val attachmentPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocument()
    ) { uri ->
        if (uri != null) {
            val mime = context.contentResolver.getType(uri) ?: "application/octet-stream"
            val fileName = resolveFileName(context, uri)
            scope.launch {
                uploadingMedia = true
                statusNotice = null
                val res = onSendMedia(uri, mime, fileName, inputText.trim())
                if (res.isSuccess) {
                    inputText = ""
                } else {
                    statusNotice = res.exceptionOrNull()?.message ?: "Upload failed"
                }
                uploadingMedia = false
            }
        }
    }

    val title = chatRow?.displayName ?: chatJid
    val unreadCount = chatRow?.chat?.unreadCount ?: 0
    val subtitle = when {
        chatRow?.isTyping == true -> "Typing..."
        chatRow?.isOnline == true -> "Online"
        (chatRow?.lastSeenTs ?: 0L) > 0L -> "Last seen ${formatShortTime(chatRow!!.lastSeenTs)}"
        else -> chatJid
    }

    Scaffold(
        containerColor = WaColors.Background,
        topBar = {
            Column(modifier = Modifier.background(WaColors.BackgroundSecondary)) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 8.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Rounded.ArrowBack,
                            contentDescription = "Back",
                            tint = WaColors.TextPrimary,
                        )
                    }
                    WaAvatar(
                        name = title,
                        avatarPath = chatRow?.resolvedAvatar.orEmpty(),
                        isOnline = chatRow?.isOnline == true,
                        size = 40.dp,
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
                            text = subtitle,
                            style = MaterialTheme.typography.labelMedium,
                            color = if (chatRow?.isTyping == true || chatRow?.isOnline == true) {
                                WaColors.Primary
                            } else {
                                WaColors.TextSecondary
                            },
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }

                    if (ghostConfig.ghostRead && unreadCount > 0) {
                        OutlinedButton(
                            onClick = onMarkReadManual,
                            modifier = Modifier.height(34.dp),
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 0.dp),
                            border = BorderStroke(WaShapes.BorderWidth, WaColors.Primary.copy(alpha = 0.6f)),
                            shape = RoundedCornerShape(WaShapes.InputRadius),
                        ) {
                            Icon(
                                Icons.Rounded.DoneAll,
                                contentDescription = null,
                                tint = WaColors.Primary,
                                modifier = Modifier.size(14.dp),
                            )
                            Spacer(Modifier.width(4.dp))
                            Text(
                                text = "Mark read",
                                fontSize = 12.sp,
                                color = WaColors.Primary,
                            )
                        }
                    }
                }
                HorizontalDivider(color = WaColors.Divider)
            }
        },
        bottomBar = {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(WaColors.BackgroundSecondary)
                    .imePadding(),
            ) {
                HorizontalDivider(color = WaColors.Divider)

                statusNotice?.let { note ->
                    Text(
                        text = note,
                        style = MaterialTheme.typography.bodyMedium,
                        color = WaColors.Danger,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    )
                }

                replyTo?.let { quoted ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(WaColors.SurfaceVariant)
                            .padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Row(
                            modifier = Modifier.weight(1f),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            Box(
                                modifier = Modifier
                                    .width(3.dp)
                                    .height(28.dp)
                                    .background(WaColors.Primary, RoundedCornerShape(2.dp)),
                            )
                            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                Text(
                                    text = if (quoted.fromMe) "You" else quoted.senderId.ifBlank { title },
                                    style = MaterialTheme.typography.labelMedium,
                                    color = WaColors.Primary,
                                    maxLines = 1,
                                )
                                Text(
                                    text = quoted.body,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = WaColors.TextSecondary,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                        IconButton(onClick = { replyTo = null }, modifier = Modifier.size(28.dp)) {
                            Icon(Icons.Rounded.Close, contentDescription = "Cancel reply", tint = WaColors.TextSecondary, modifier = Modifier.size(16.dp))
                        }
                    }
                }

                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 10.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    IconButton(
                        onClick = {
                            attachmentPicker.launch(arrayOf("image/*", "video/*", "audio/*", "*/*"))
                        },
                        enabled = !uploadingMedia,
                    ) {
                        if (uploadingMedia) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                strokeWidth = 2.dp,
                                color = WaColors.Primary,
                            )
                        } else {
                            Icon(
                                Icons.Rounded.AttachFile,
                                contentDescription = "Attach",
                                tint = WaColors.TextSecondary,
                            )
                        }
                    }

                    OutlinedTextField(
                        value = inputText,
                        onValueChange = { next ->
                            val wasBlank = inputText.isBlank()
                            inputText = next
                            val nowBlank = next.isBlank()
                            if (wasBlank != nowBlank) {
                                onTypingChanged(!nowBlank)
                            }
                        },
                        modifier = Modifier.weight(1f),
                        maxLines = 4,
                        placeholder = { Text("Message", color = WaColors.TextMuted) },
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

                    Surface(
                        color = if (inputText.isNotBlank()) WaColors.ButtonBackground else WaColors.SurfaceElevated,
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                        modifier = Modifier.size(48.dp),
                    ) {
                        IconButton(
                            onClick = {
                                val text = inputText.trim()
                                if (text.isNotEmpty()) {
                                    val qid = replyTo?.id.orEmpty()
                                    inputText = ""
                                    replyTo = null
                                    onTypingChanged(false)
                                    scope.launch { onSendText(text, qid) }
                                }
                            },
                            enabled = inputText.isNotBlank(),
                        ) {
                            Icon(
                                Icons.AutoMirrored.Rounded.Send,
                                contentDescription = "Send",
                                tint = if (inputText.isNotBlank()) WaColors.ButtonText else WaColors.TextMuted,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                }
            }
        },
    ) { innerPadding ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
            reverseLayout = true,
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(
                items = reversedMessages,
                key = { it.id },
            ) { msg ->
                val quotedMsg = msg.quotedId.takeIf { it.isNotBlank() }?.let { messagesById[it] }
                MessageBubbleRow(
                    msg = msg,
                    quotedMsg = quotedMsg,
                    isDownloading = msg.id in downloadingIds,
                    onSwipeReply = { replyTo = msg },
                    onLongPress = { selectedMsgForSheet = msg },
                    onDownloadClick = {
                        scope.launch {
                            downloadingIds = downloadingIds + msg.id
                            onDownloadMedia(msg.id)
                            downloadingIds = downloadingIds - msg.id
                        }
                    },
                    onOpenMedia = { mediaViewerMsg = msg },
                )
            }
        }
    }

    selectedMsgForSheet?.let { msg ->
        ModalBottomSheet(
            onDismissRequest = { selectedMsgForSheet = null },
            containerColor = WaColors.Surface,
            contentColor = WaColors.TextPrimary,
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceEvenly,
                ) {
                    QUICK_REACTIONS.forEach { emoji ->
                        Surface(
                            color = WaColors.SurfaceVariant,
                            shape = CircleShape,
                            border = BorderStroke(1.dp, WaColors.CardBorder),
                            modifier = Modifier
                                .size(44.dp)
                                .clickable {
                                    scope.launch {
                                        onSendReaction(msg.id, msg.senderId.ifBlank { chatJid }, emoji)
                                    }
                                    selectedMsgForSheet = null
                                },
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Text(text = emoji, fontSize = 20.sp)
                            }
                        }
                    }
                }

                HorizontalDivider(color = WaColors.Divider)

                TextButton(
                    onClick = {
                        replyTo = msg
                        selectedMsgForSheet = null
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Icon(Icons.AutoMirrored.Rounded.Reply, contentDescription = null, tint = WaColors.TextSecondary, modifier = Modifier.size(18.dp))
                        Text("Reply", color = WaColors.TextPrimary)
                    }
                }

                TextButton(
                    onClick = {
                        clipboard.setText(AnnotatedString(msg.originalBody.ifBlank { msg.body }))
                        selectedMsgForSheet = null
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Icon(Icons.Rounded.ContentCopy, contentDescription = null, tint = WaColors.TextSecondary, modifier = Modifier.size(18.dp))
                        Text("Copy text", color = WaColors.TextPrimary)
                    }
                }
                Spacer(Modifier.height(16.dp))
            }
        }
    }

    mediaViewerMsg?.let { msg ->
        MediaViewerDialog(
            msg = msg,
            onDismiss = { mediaViewerMsg = null },
        )
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun MessageBubbleRow(
    msg: MessageEntity,
    quotedMsg: MessageEntity?,
    isDownloading: Boolean,
    onSwipeReply: () -> Unit,
    onLongPress: () -> Unit,
    onDownloadClick: () -> Unit,
    onOpenMedia: () -> Unit,
) {
    var dragOffset by remember { mutableFloatStateOf(0f) }
    val reactions = remember(msg.reactionsJson) { parseReactions(msg.reactionsJson) }

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .pointerInput(msg.id) {
                detectHorizontalDragGestures(
                    onDragEnd = {
                        if (dragOffset > 90f) {
                            onSwipeReply()
                        }
                        dragOffset = 0f
                    },
                    onDragCancel = { dragOffset = 0f },
                    onHorizontalDrag = { _, delta ->
                        dragOffset = (dragOffset + delta).coerceIn(0f, 130f)
                    },
                )
            },
        contentAlignment = if (msg.fromMe) Alignment.CenterEnd else Alignment.CenterStart,
    ) {
        Column(
            modifier = Modifier
                .offset { IntOffset(dragOffset.roundToInt(), 0) }
                .widthIn(max = 310.dp),
            horizontalAlignment = if (msg.fromMe) Alignment.End else Alignment.Start,
        ) {
            Surface(
                color = if (msg.fromMe) WaColors.SurfaceElevated else WaColors.CardBackground,
                border = BorderStroke(
                    WaShapes.BorderWidth,
                    if (msg.isDeleted) WaColors.Danger.copy(alpha = 0.45f)
                    else if (msg.fromMe) WaColors.InputBorder
                    else WaColors.CardBorder,
                ),
                shape = RoundedCornerShape(WaShapes.CardRadius),
                modifier = Modifier.combinedClickable(
                    onClick = {
                        if (msg.mediaPath.isNotBlank() && File(msg.mediaPath).exists()) {
                            onOpenMedia()
                        }
                    },
                    onLongClick = onLongPress,
                ),
            ) {
                Column(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (!msg.fromMe && msg.senderId.isNotBlank() && msg.chatId.endsWith("@g.us")) {
                        Text(
                            text = msg.senderId.substringBefore('@'),
                            style = MaterialTheme.typography.labelMedium,
                            color = WaColors.Primary,
                        )
                    }

                    if (msg.isDeleted || msg.isEdited || msg.isViewOnce) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            if (msg.isDeleted) {
                                StatusTag("Deleted", WaColors.Danger)
                            }
                            if (msg.isEdited) {
                                StatusTag("Edited", WaColors.Warning)
                            }
                            if (msg.isViewOnce) {
                                StatusTag("View-once", WaColors.Primary)
                            }
                        }
                    }

                    if (quotedMsg != null) {
                        Surface(
                            color = WaColors.BackgroundSecondary,
                            shape = RoundedCornerShape(8.dp),
                            border = BorderStroke(1.dp, WaColors.Divider),
                        ) {
                            Column(modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp)) {
                                Text(
                                    text = if (quotedMsg.fromMe) "You" else quotedMsg.senderId.ifBlank { "Reply" },
                                    style = MaterialTheme.typography.labelMedium,
                                    color = WaColors.Primary,
                                    maxLines = 1,
                                )
                                Text(
                                    text = quotedMsg.body,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = WaColors.TextSecondary,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }

                    if (msg.msgType in setOf("image", "sticker", "video", "audio", "document")) {
                        MediaAttachmentBlock(
                            msg = msg,
                            isDownloading = isDownloading,
                            onDownloadClick = onDownloadClick,
                            onOpenMedia = onOpenMedia,
                        )
                    }

                    val displayText = if (msg.isDeleted && msg.originalBody.isNotBlank()) {
                        msg.originalBody
                    } else {
                        msg.body
                    }
                    if (displayText.isNotBlank()) {
                        Text(
                            text = displayText,
                            style = MaterialTheme.typography.bodyLarge,
                            color = WaColors.TextPrimary,
                        )
                    }

                    if (msg.isEdited && msg.originalBody.isNotBlank() && msg.originalBody != msg.body) {
                        Text(
                            text = "Original: ${msg.originalBody}",
                            style = MaterialTheme.typography.bodyMedium,
                            color = WaColors.TextMuted,
                        )
                    }

                    Row(
                        modifier = Modifier.align(Alignment.End),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(
                            text = formatMessageTime(msg.ts),
                            style = MaterialTheme.typography.labelMedium,
                            color = WaColors.TextMuted,
                        )
                        if (msg.fromMe) {
                            val receiptColor = when (msg.receiptStatus) {
                                "read", "played" -> WaColors.Primary
                                "failed" -> WaColors.Danger
                                else -> WaColors.TextMuted
                            }
                            Text(
                                text = msg.receiptStatus,
                                style = MaterialTheme.typography.labelMedium,
                                color = receiptColor,
                            )
                        }
                    }
                }
            }

            if (reactions.isNotEmpty()) {
                Surface(
                    color = WaColors.Surface,
                    shape = CircleShape,
                    border = BorderStroke(1.dp, WaColors.CardBorder),
                    modifier = Modifier.padding(top = 2.dp),
                ) {
                    Text(
                        text = reactions.entries.joinToString(" ") { (emoji, count) ->
                            if (count > 1) "$emoji $count" else emoji
                        },
                        fontSize = 12.sp,
                        color = WaColors.TextPrimary,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun MediaAttachmentBlock(
    msg: MessageEntity,
    isDownloading: Boolean,
    onDownloadClick: () -> Unit,
    onOpenMedia: () -> Unit,
) {
    val hasLocalFile = msg.mediaPath.isNotBlank() && File(msg.mediaPath).exists()
    when {
        (msg.msgType == "image" || msg.msgType == "sticker") && hasLocalFile -> {
            AsyncImage(
                model = File(msg.mediaPath),
                contentDescription = msg.msgType,
                contentScale = ContentScale.Crop,
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 120.dp, max = 220.dp)
                    .clip(RoundedCornerShape(WaShapes.InputRadius))
                    .clickable(onClick = onOpenMedia),
            )
        }

        (msg.msgType == "video" || msg.msgType == "audio") && hasLocalFile -> {
            Surface(
                color = WaColors.BackgroundSecondary,
                shape = RoundedCornerShape(WaShapes.InputRadius),
                border = BorderStroke(1.dp, WaColors.Divider),
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(onClick = onOpenMedia),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Icon(Icons.Rounded.PlayArrow, contentDescription = "Play", tint = WaColors.Primary, modifier = Modifier.size(20.dp))
                    Text(
                        text = if (msg.msgType == "video") "Play video" else "Play audio",
                        style = MaterialTheme.typography.bodyMedium,
                        color = WaColors.TextPrimary,
                    )
                }
            }
        }

        msg.msgType == "document" && hasLocalFile -> {
            Surface(
                color = WaColors.BackgroundSecondary,
                shape = RoundedCornerShape(WaShapes.InputRadius),
                border = BorderStroke(1.dp, WaColors.Divider),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Icon(Icons.Rounded.Description, contentDescription = null, tint = WaColors.Primary, modifier = Modifier.size(18.dp))
                    Text(
                        text = File(msg.mediaPath).name,
                        style = MaterialTheme.typography.bodyMedium,
                        color = WaColors.TextPrimary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }

        msg.rawJson.isNotBlank() -> {
            OutlinedButton(
                onClick = onDownloadClick,
                enabled = !isDownloading,
                border = BorderStroke(1.dp, WaColors.CardBorder),
                shape = RoundedCornerShape(WaShapes.InputRadius),
            ) {
                if (isDownloading) {
                    CircularProgressIndicator(modifier = Modifier.size(14.dp), strokeWidth = 2.dp, color = WaColors.Primary)
                    Spacer(Modifier.width(8.dp))
                    Text("Downloading", color = WaColors.TextSecondary)
                } else {
                    Icon(Icons.Rounded.Download, contentDescription = null, tint = WaColors.Primary, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Load ${msg.msgType}", color = WaColors.TextPrimary)
                }
            }
        }
    }
}

@Composable
private fun MediaViewerDialog(
    msg: MessageEntity,
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current
    val file = File(msg.mediaPath)

    Dialog(onDismissRequest = onDismiss) {
        Surface(
            color = WaColors.Surface,
            shape = RoundedCornerShape(WaShapes.CardRadius),
            border = BorderStroke(1.dp, WaColors.CardBorder),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        text = msg.msgType.replaceFirstChar { it.uppercase() },
                        style = MaterialTheme.typography.titleMedium,
                        color = WaColors.TextPrimary,
                    )
                    IconButton(onClick = onDismiss) {
                        Icon(Icons.Rounded.Close, contentDescription = "Close", tint = WaColors.TextSecondary)
                    }
                }

                if (msg.msgType == "image" || msg.msgType == "sticker") {
                    AsyncImage(
                        model = file,
                        contentDescription = msg.msgType,
                        contentScale = ContentScale.Fit,
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 420.dp)
                            .clip(RoundedCornerShape(WaShapes.InputRadius)),
                    )
                } else if (msg.msgType == "video" || msg.msgType == "audio") {
                    val player = remember(msg.mediaPath) {
                        ExoPlayer.Builder(context).build().apply {
                            setMediaItem(MediaItem.fromUri(Uri.fromFile(file)))
                            prepare()
                            playWhenReady = true
                        }
                    }
                    DisposableEffect(player) {
                        onDispose { player.release() }
                    }
                    AndroidView(
                        factory = { ctx ->
                            PlayerView(ctx).apply {
                                this.player = player
                                useController = true
                            }
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(if (msg.msgType == "video") 260.dp else 96.dp)
                            .clip(RoundedCornerShape(WaShapes.InputRadius)),
                    )
                } else {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Icon(Icons.Rounded.Stop, contentDescription = null, tint = WaColors.TextSecondary)
                        Text(file.absolutePath, style = MaterialTheme.typography.labelMedium, color = WaColors.TextSecondary)
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusTag(text: String, color: androidx.compose.ui.graphics.Color) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelMedium,
        color = color,
    )
}

private fun parseReactions(reactionsJson: String): Map<String, Int> {
    if (reactionsJson.isBlank() || reactionsJson == "{}") return emptyMap()
    return runCatching {
        val obj = JSONObject(reactionsJson)
        val counts = LinkedHashMap<String, Int>()
        val keys = obj.keys()
        while (keys.hasNext()) {
            val k = keys.next()
            val emoji = obj.optString(k, "")
            if (emoji.isNotBlank()) {
                counts[emoji] = (counts[emoji] ?: 0) + 1
            }
        }
        counts
    }.getOrDefault(emptyMap())
}

private fun resolveFileName(context: android.content.Context, uri: Uri): String {
    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) {
            val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (idx >= 0) return c.getString(idx).orEmpty()
        }
    }
    return uri.lastPathSegment ?: "attachment"
}
