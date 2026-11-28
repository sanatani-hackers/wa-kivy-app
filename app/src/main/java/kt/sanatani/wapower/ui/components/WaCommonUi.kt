package kt.sanatani.wapower.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Build
import androidx.compose.material.icons.rounded.Call
import androidx.compose.material.icons.rounded.ChatBubbleOutline
import androidx.compose.material.icons.rounded.Contacts
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import kt.sanatani.wapower.ui.theme.WaColors
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

enum class MainTab(val label: String, val icon: ImageVector) {
    CHATS("Chats", Icons.Rounded.ChatBubbleOutline),
    CONTACTS("Contacts", Icons.Rounded.Contacts),
    CALLS("Calls", Icons.Rounded.Call),
    TOOLS("Tools", Icons.Rounded.Build),
    SETTINGS("Settings", Icons.Rounded.Settings),
}

@Composable
fun WaBottomBar(
    currentTab: MainTab,
    onSelectTab: (MainTab) -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth()) {
        HorizontalDivider(color = WaColors.Divider, thickness = 1.dp)
        NavigationBar(
            containerColor = WaColors.NavbarBackground,
            tonalElevation = 0.dp,
        ) {
            MainTab.entries.forEach { tab ->
                val selected = tab == currentTab
                NavigationBarItem(
                    selected = selected,
                    onClick = { onSelectTab(tab) },
                    icon = {
                        Icon(
                            imageVector = tab.icon,
                            contentDescription = tab.label,
                            modifier = Modifier.size(20.dp),
                        )
                    },
                    label = {
                        Text(
                            text = tab.label,
                            fontSize = 11.sp,
                            fontWeight = if (selected) FontWeight.Medium else FontWeight.Normal,
                            maxLines = 1,
                        )
                    },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = WaColors.NavbarSelected,
                        selectedTextColor = WaColors.NavbarSelected,
                        unselectedIconColor = WaColors.NavbarUnselected,
                        unselectedTextColor = WaColors.NavbarUnselected,
                        indicatorColor = WaColors.SurfaceElevated,
                    ),
                )
            }
        }
    }
}

@Composable
fun WaAvatar(
    name: String,
    avatarPath: String,
    isOnline: Boolean = false,
    size: Dp = 44.dp,
) {
    val initials = name
        .trim()
        .split(Regex("\\s+"))
        .filter { it.isNotBlank() }
        .take(2)
        .joinToString("") { it.first().uppercaseChar().toString() }
        .ifBlank { "?" }

    Box(modifier = Modifier.size(size)) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            shape = CircleShape,
            color = WaColors.SurfaceElevated,
            border = BorderStroke(1.dp, WaColors.CardBorder),
        ) {
            if (avatarPath.isNotBlank()) {
                val model: Any = if (avatarPath.startsWith("http", ignoreCase = true)) {
                    avatarPath
                } else {
                    File(avatarPath)
                }
                AsyncImage(
                    model = model,
                    contentDescription = name,
                    contentScale = ContentScale.Crop,
                    modifier = Modifier
                        .fillMaxSize()
                        .clip(CircleShape),
                )
            } else {
                Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxSize()) {
                    Text(
                        text = initials,
                        style = TextStyle(
                            fontFamily = FontFamily.SansSerif,
                            fontWeight = FontWeight.Medium,
                            fontSize = (size.value * 0.36f).sp,
                        ),
                        color = WaColors.PrimaryLight,
                    )
                }
            }
        }

        if (isOnline) {
            Box(
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(WaColors.Primary)
                    .border(2.dp, WaColors.Background, CircleShape),
            )
        }
    }
}

fun formatShortTime(epochSec: Long): String {
    if (epochSec <= 0L) return ""
    val millis = epochSec * 1000L
    val now = System.currentTimeMillis()
    val diffDays = (now - millis) / 86_400_000L
    val fmt = when {
        diffDays < 1L -> SimpleDateFormat("HH:mm", Locale.US)
        diffDays < 7L -> SimpleDateFormat("EEE", Locale.US)
        else -> SimpleDateFormat("dd/MM", Locale.US)
    }
    return fmt.format(Date(millis))
}

fun formatMessageTime(epochSec: Long): String {
    if (epochSec <= 0L) return ""
    return SimpleDateFormat("HH:mm", Locale.US).format(Date(epochSec * 1000L))
}
