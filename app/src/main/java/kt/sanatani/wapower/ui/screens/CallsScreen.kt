package kt.sanatani.wapower.ui.screens

import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Add
import androidx.compose.material.icons.rounded.AudioFile
import androidx.compose.material.icons.rounded.Call
import androidx.compose.material.icons.rounded.CallEnd
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.MusicNote
import androidx.compose.material.icons.rounded.Pause
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material.icons.rounded.SkipNext
import androidx.compose.material.icons.rounded.Stop
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
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kt.sanatani.wapower.calls.ActiveCallInfo
import kt.sanatani.wapower.calls.BufferProfile
import kt.sanatani.wapower.calls.CallDeckState
import kt.sanatani.wapower.calls.VideoQuality
import kt.sanatani.wapower.db.CallLogEntity
import kt.sanatani.wapower.ui.components.formatShortTime
import kt.sanatani.wapower.ui.theme.WaColors
import kt.sanatani.wapower.ui.theme.WaShapes

@Composable
fun CallsScreen(
    deck: CallDeckState,
    callHistory: List<CallLogEntity>,
    onPlaceCall: (target: String, isVideo: Boolean) -> Unit,
    onAnswerCall: (callId: String) -> Unit,
    onAnswerWithMusic: (callId: String) -> Unit = {},
    onDeclineCall: (callId: String) -> Unit,
    onHangupCall: (callId: String) -> Unit,
    onPauseAudio: (callId: String) -> Unit,
    onResumeAudio: (callId: String) -> Unit,
    onStopMedia: (callId: String) -> Unit,
    onSkipNext: (callId: String) -> Unit,
    onSearchYouTube: (query: String) -> Unit,
    onStreamYtAudio: (callId: String, queryOrUrl: String) -> Unit,
    onStreamYtVideo: (callId: String, queryOrUrl: String) -> Unit,
    onEnqueueYt: (queryOrUrl: String, title: String, asVideo: Boolean) -> Unit,
    onRemoveQueued: (uid: String) -> Unit,
    onPlayLocalMedia: (callId: String, uri: Uri, mimeType: String, displayName: String) -> Unit,
    onSetBufferProfile: (BufferProfile) -> Unit,
    onSetVideoQuality: (VideoQuality) -> Unit,
    onSetLoopTrack: (Boolean) -> Unit,
) {
    val context = LocalContext.current
    var dialTarget by rememberSaveable { mutableStateOf("") }
    var ytInput by rememberSaveable { mutableStateOf("") }

    val selectedCall = deck.activeCalls.firstOrNull { it.callId == deck.selectedCallId }
        ?: deck.activeCalls.firstOrNull()
    val latestCallId by rememberUpdatedState(selectedCall?.callId.orEmpty())

    val localTrackPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.OpenDocument()
    ) { uri ->
        val cid = latestCallId.ifBlank { return@rememberLauncherForActivityResult }
        if (uri != null) {
            val mime = runCatching { context.contentResolver.getType(uri) }.getOrNull() ?: "audio/*"
            val name = resolveTrackName(context, uri)
            onPlayLocalMedia(cid, uri, mime, name)
        }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .background(WaColors.Background),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item(key = "header") {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(
                        text = "Calls & Stream",
                        style = MaterialTheme.typography.headlineMedium,
                        color = WaColors.TextPrimary,
                    )
                    Text(
                        text = if (deck.activeCalls.isNotEmpty()) {
                            "${deck.activeCalls.size} active call"
                        } else {
                            "VoIP & YouTube live deck"
                        },
                        style = MaterialTheme.typography.labelMedium,
                        color = WaColors.TextSecondary,
                    )
                }
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(if (deck.activeCalls.isNotEmpty()) WaColors.Primary else WaColors.TextMuted),
                )
            }
        }

        if (deck.statusNote.isNotBlank()) {
            item(key = "status_note") {
                Text(
                    text = deck.statusNote,
                    style = MaterialTheme.typography.labelMedium,
                    color = WaColors.PrimaryLight,
                )
            }
        }

        // Active Calls Deck
        items(deck.activeCalls, key = { "active_${it.callId}" }) { call ->
            ActiveCallCard(
                call = call,
                onAnswer = { onAnswerCall(call.callId) },
                onAnswerWithMusic = { onAnswerWithMusic(call.callId) },
                onDecline = { onDeclineCall(call.callId) },
                onHangup = { onHangupCall(call.callId) },
                onPause = { onPauseAudio(call.callId) },
                onResume = { onResumeAudio(call.callId) },
                onStop = { onStopMedia(call.callId) },
                onSkip = { onSkipNext(call.callId) },
            )
        }

        // Dialer Card
        item(key = "dialer") {
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
                    Text("Place call", style = MaterialTheme.typography.titleMedium, color = WaColors.TextPrimary)
                    OutlinedTextField(
                        value = dialTarget,
                        onValueChange = { dialTarget = it },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                        placeholder = { Text("919876543210 or JID", color = WaColors.TextMuted) },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text),
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
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Button(
                            onClick = { onPlaceCall(dialTarget, false) },
                            modifier = Modifier.weight(1f).height(44.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = WaColors.ButtonBackground,
                                contentColor = WaColors.ButtonText,
                            ),
                            shape = RoundedCornerShape(WaShapes.ButtonRadius),
                        ) {
                            Icon(Icons.Rounded.Call, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Voice", fontWeight = FontWeight.Medium)
                        }
                        OutlinedButton(
                            onClick = { onPlaceCall(dialTarget, true) },
                            modifier = Modifier.weight(1f).height(44.dp),
                            border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                            shape = RoundedCornerShape(WaShapes.ButtonRadius),
                        ) {
                            Icon(Icons.Rounded.Videocam, contentDescription = null, tint = WaColors.Primary, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Video", color = WaColors.TextPrimary, fontWeight = FontWeight.Medium)
                        }
                    }
                }
            }
        }

        // YouTube Live Video / Voice Streamer Deck
        item(key = "yt_deck") {
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
                        Text(
                            text = "YouTube live stream",
                            style = MaterialTheme.typography.titleMedium,
                            color = WaColors.TextPrimary,
                        )
                        if (selectedCall != null) {
                            OutlinedButton(
                                onClick = {
                                    runCatching { localTrackPicker.launch(arrayOf("audio/*", "video/*")) }
                                        .onFailure {
                                            runCatching { localTrackPicker.launch(arrayOf("*/*")) }
                                        }
                                },
                                modifier = Modifier.height(34.dp),
                                contentPadding = PaddingValues(horizontal = 10.dp),
                                border = BorderStroke(1.dp, WaColors.CardBorder),
                                shape = RoundedCornerShape(WaShapes.InputRadius),
                            ) {
                                Icon(Icons.Rounded.AudioFile, contentDescription = null, tint = WaColors.Primary, modifier = Modifier.size(15.dp))
                                Spacer(Modifier.width(4.dp))
                                Text("Local file", color = WaColors.TextPrimary)
                            }
                        }
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        OutlinedTextField(
                            value = ytInput,
                            onValueChange = { ytInput = it },
                            modifier = Modifier.weight(1f),
                            singleLine = true,
                            placeholder = { Text("Search song or YouTube URL", color = WaColors.TextMuted) },
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                            keyboardActions = KeyboardActions(onSearch = { onSearchYouTube(ytInput) }),
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
                        IconButton(
                            onClick = { onSearchYouTube(ytInput) },
                            enabled = !deck.searching,
                        ) {
                            if (deck.searching) {
                                CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp, color = WaColors.Primary)
                            } else {
                                Icon(Icons.Rounded.Search, contentDescription = "Search", tint = WaColors.Primary)
                            }
                        }
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Button(
                            onClick = {
                                val cid = selectedCall?.callId ?: return@Button
                                onStreamYtAudio(cid, ytInput)
                            },
                            enabled = selectedCall != null && ytInput.isNotBlank() && !deck.streamingBusy,
                            modifier = Modifier.weight(1f).height(40.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = WaColors.ButtonBackground,
                                contentColor = WaColors.ButtonText,
                            ),
                            shape = RoundedCornerShape(WaShapes.ButtonRadius),
                        ) {
                            Icon(Icons.Rounded.MusicNote, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Stream audio")
                        }
                        OutlinedButton(
                            onClick = {
                                val cid = selectedCall?.callId ?: return@OutlinedButton
                                onStreamYtVideo(cid, ytInput)
                            },
                            enabled = selectedCall != null && ytInput.isNotBlank() && !deck.streamingBusy,
                            modifier = Modifier.weight(1f).height(40.dp),
                            border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                            shape = RoundedCornerShape(WaShapes.ButtonRadius),
                        ) {
                            Icon(Icons.Rounded.Videocam, contentDescription = null, tint = WaColors.Primary, modifier = Modifier.size(16.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Stream video", color = WaColors.TextPrimary)
                        }
                    }

                    HorizontalDivider(color = WaColors.Divider)

                    Text("Stream settings", style = MaterialTheme.typography.bodyMedium, color = WaColors.TextSecondary)
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        items(VideoQuality.entries, key = { it.name }) { q ->
                            val selected = deck.videoQuality == q
                            FilterChip(
                                selected = selected,
                                onClick = { onSetVideoQuality(q) },
                                label = { Text(q.label) },
                                shape = RoundedCornerShape(WaShapes.InputRadius),
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = WaColors.SurfaceElevated,
                                    selectedLabelColor = WaColors.Primary,
                                    containerColor = WaColors.Surface,
                                    labelColor = WaColors.TextSecondary,
                                ),
                            )
                        }
                        items(BufferProfile.entries, key = { it.name }) { bp ->
                            val selected = deck.bufferProfile == bp
                            FilterChip(
                                selected = selected,
                                onClick = { onSetBufferProfile(bp) },
                                label = { Text(bp.label) },
                                shape = RoundedCornerShape(WaShapes.InputRadius),
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = WaColors.SurfaceElevated,
                                    selectedLabelColor = WaColors.Primary,
                                    containerColor = WaColors.Surface,
                                    labelColor = WaColors.TextSecondary,
                                ),
                            )
                        }
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text("Loop local track", style = MaterialTheme.typography.bodyMedium, color = WaColors.TextSecondary)
                        Switch(
                            checked = deck.loopTrack,
                            onCheckedChange = onSetLoopTrack,
                            colors = SwitchDefaults.colors(
                                checkedThumbColor = WaColors.ButtonText,
                                checkedTrackColor = WaColors.Primary,
                                uncheckedThumbColor = WaColors.TextSecondary,
                                uncheckedTrackColor = WaColors.SurfaceElevated,
                            ),
                        )
                    }
                }
            }
        }

        // YouTube Search Hits
        if (deck.searchResults.isNotEmpty()) {
            item(key = "yt_hits_hdr") {
                Text("Search results", style = MaterialTheme.typography.titleMedium, color = WaColors.TextPrimary)
            }
            items(deck.searchResults, key = { "yt_${it.id}" }) { hit ->
                Card(
                    colors = CardDefaults.cardColors(containerColor = WaColors.CardBackground),
                    border = BorderStroke(WaShapes.BorderWidth, WaColors.CardBorder),
                    shape = RoundedCornerShape(WaShapes.CardRadius),
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(
                                text = hit.title,
                                style = MaterialTheme.typography.bodyLarge,
                                color = WaColors.TextPrimary,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                text = "${hit.author.ifBlank { "YouTube" }}  •  ${hit.durationText}",
                                style = MaterialTheme.typography.labelMedium,
                                color = WaColors.TextSecondary,
                            )
                        }
                        if (selectedCall != null) {
                            IconButton(onClick = { onStreamYtAudio(selectedCall.callId, hit.url) }) {
                                Icon(Icons.Rounded.MusicNote, contentDescription = "Stream audio", tint = WaColors.Primary, modifier = Modifier.size(18.dp))
                            }
                            IconButton(onClick = { onStreamYtVideo(selectedCall.callId, hit.url) }) {
                                Icon(Icons.Rounded.Videocam, contentDescription = "Stream video", tint = WaColors.TextSecondary, modifier = Modifier.size(18.dp))
                            }
                        }
                        IconButton(onClick = { onEnqueueYt(hit.url, hit.title, false) }) {
                            Icon(Icons.Rounded.Add, contentDescription = "Queue", tint = WaColors.TextSecondary, modifier = Modifier.size(18.dp))
                        }
                    }
                }
            }
        }

        // Queue
        if (deck.queue.isNotEmpty()) {
            item(key = "queue_hdr") {
                Text("DJ queue (${deck.queue.size})", style = MaterialTheme.typography.titleMedium, color = WaColors.TextPrimary)
            }
            items(deck.queue, key = { "queue_${it.uid}" }) { item ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(WaColors.CardBackground, RoundedCornerShape(WaShapes.InputRadius))
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        text = item.title,
                        style = MaterialTheme.typography.bodyMedium,
                        color = WaColors.TextPrimary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    IconButton(onClick = { onRemoveQueued(item.uid) }, modifier = Modifier.size(28.dp)) {
                        Icon(Icons.Rounded.Close, contentDescription = "Remove", tint = WaColors.TextMuted, modifier = Modifier.size(16.dp))
                    }
                }
            }
        }

        // Call History
        item(key = "history_hdr") {
            Text("Recent calls", style = MaterialTheme.typography.titleMedium, color = WaColors.TextPrimary)
        }
        if (callHistory.isEmpty()) {
            item(key = "history_empty") {
                Text("No call logs yet", style = MaterialTheme.typography.bodyMedium, color = WaColors.TextMuted)
            }
        } else {
            items(callHistory, key = { "hist_${it.callId}" }) { log ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(
                            text = log.peerJid.substringBefore('@').substringBefore(':').ifBlank { log.peerJid },
                            style = MaterialTheme.typography.bodyLarge,
                            color = WaColors.TextPrimary,
                        )
                        Text(
                            text = "${log.direction} • ${if (log.isVideo) "video" else "voice"} • ${log.status}",
                            style = MaterialTheme.typography.labelMedium,
                            color = WaColors.TextSecondary,
                        )
                    }
                    Text(
                        text = formatShortTime(log.startedAt),
                        style = MaterialTheme.typography.labelMedium,
                        color = WaColors.TextMuted,
                    )
                }
                HorizontalDivider(color = WaColors.Divider)
            }
        }
    }
}

@Composable
private fun ActiveCallCard(
    call: ActiveCallInfo,
    onAnswer: () -> Unit,
    onAnswerWithMusic: () -> Unit,
    onDecline: () -> Unit,
    onHangup: () -> Unit,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onStop: () -> Unit,
    onSkip: () -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(containerColor = WaColors.SurfaceElevated),
        border = BorderStroke(WaShapes.BorderWidth, WaColors.Primary.copy(alpha = 0.5f)),
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
                        text = call.peerDisplay.ifBlank { call.peer },
                        style = MaterialTheme.typography.titleMedium,
                        color = WaColors.TextPrimary,
                    )
                    Text(
                        text = "${call.direction} ${if (call.isVideo) "video" else "voice"} • ${call.phase}",
                        style = MaterialTheme.typography.labelMedium,
                        color = WaColors.Primary,
                    )
                }
                Box(
                    modifier = Modifier
                        .size(8.dp)
                        .clip(CircleShape)
                        .background(WaColors.Primary),
                )
            }

            if (call.nowPlaying.isNotBlank()) {
                Text(
                    text = "Streaming: ${call.nowPlaying} (${call.audioState})",
                    style = MaterialTheme.typography.bodyMedium,
                    color = WaColors.TextSecondary,
                )
            }

            if (call.direction == "in" && call.phase !in setOf("active", "ended")) {
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
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
                        border = BorderStroke(1.dp, WaColors.Danger.copy(alpha = 0.5f)),
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                    ) {
                        Text("Decline", color = WaColors.Danger)
                    }
                }
            } else {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    if (call.audioState == "playing") {
                        OutlinedButton(onClick = onPause, shape = RoundedCornerShape(WaShapes.InputRadius)) {
                            Icon(Icons.Rounded.Pause, contentDescription = "Pause", tint = WaColors.TextPrimary, modifier = Modifier.size(16.dp))
                        }
                    } else if (call.audioState == "paused") {
                        OutlinedButton(onClick = onResume, shape = RoundedCornerShape(WaShapes.InputRadius)) {
                            Icon(Icons.Rounded.PlayArrow, contentDescription = "Resume", tint = WaColors.Primary, modifier = Modifier.size(16.dp))
                        }
                    }
                    if (call.audioState != "idle") {
                        OutlinedButton(onClick = onStop, shape = RoundedCornerShape(WaShapes.InputRadius)) {
                            Icon(Icons.Rounded.Stop, contentDescription = "Stop media", tint = WaColors.TextSecondary, modifier = Modifier.size(16.dp))
                        }
                        OutlinedButton(onClick = onSkip, shape = RoundedCornerShape(WaShapes.InputRadius)) {
                            Icon(Icons.Rounded.SkipNext, contentDescription = "Skip", tint = WaColors.TextSecondary, modifier = Modifier.size(16.dp))
                        }
                    }
                    Spacer(Modifier.weight(1f))
                    Button(
                        onClick = onHangup,
                        colors = ButtonDefaults.buttonColors(containerColor = WaColors.Danger, contentColor = WaColors.TextPrimary),
                        shape = RoundedCornerShape(WaShapes.ButtonRadius),
                    ) {
                        Icon(Icons.Rounded.CallEnd, contentDescription = null, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("Hang up")
                    }
                }
            }
        }
    }
}

private fun resolveTrackName(context: android.content.Context, uri: Uri): String {
    runCatching {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                val idx = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (idx >= 0) {
                    val v = c.getString(idx).orEmpty().trim()
                    if (v.isNotEmpty()) return v
                }
            }
        }
    }
    return uri.lastPathSegment?.substringAfterLast('/')?.ifBlank { null } ?: "track"
}
