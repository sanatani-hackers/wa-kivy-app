package kt.sanatani.wapower.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

object WaColors {
    val Background = Color(0xFF080C14)
    val Surface = Color(0xFF0F172A)
    val SurfaceElevated = Color(0xFF162033)
    val Stroke = Color(0xFF1E293B)
    val Emerald = Color(0xFF10B981)
    val EmeraldDark = Color(0xFF059669)
    val SkyInfo = Color(0xFF38BDF8)
    val AmberWarn = Color(0xFFF59E0B)
    val Danger = Color(0xFFEF4444)
    val TextPrimary = Color(0xFFF1F5F9)
    val TextSecondary = Color(0xFF94A3B8)
    val TextMuted = Color(0xFF64748B)
}

private val ObsidianDarkColorScheme = darkColorScheme(
    primary = WaColors.Emerald,
    onPrimary = Color(0xFF032318),
    primaryContainer = Color(0xFF064E3B),
    onPrimaryContainer = Color(0xFFA7F3D0),
    secondary = WaColors.SkyInfo,
    onSecondary = Color(0xFF06202E),
    secondaryContainer = Color(0xFF0C4A6E),
    onSecondaryContainer = Color(0xFFBAE6FD),
    tertiary = WaColors.AmberWarn,
    onTertiary = Color(0xFF2B1900),
    background = WaColors.Background,
    onBackground = WaColors.TextPrimary,
    surface = WaColors.Surface,
    onSurface = WaColors.TextPrimary,
    surfaceVariant = WaColors.SurfaceElevated,
    onSurfaceVariant = WaColors.TextSecondary,
    outline = WaColors.Stroke,
    outlineVariant = WaColors.Stroke,
    error = WaColors.Danger,
    onError = Color.White,
)

private val WaTypography = Typography(
    headlineMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 24.sp,
        lineHeight = 32.sp,
        letterSpacing = (-0.3).sp,
    ),
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 20.sp,
        lineHeight = 28.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 22.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 18.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.Medium,
        fontSize = 12.sp,
        lineHeight = 16.sp,
    ),
)

@Composable
fun WaPowerTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = ObsidianDarkColorScheme,
        typography = WaTypography,
        content = content,
    )
}
