package kt.sanatani.wapower.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Obsidian Imperial — Luxury Hacker dark palette & craft tokens.
 */
object WaColors {
    val Background = Color(0xFF000000)
    val BackgroundSecondary = Color(0xFF080808)
    val Surface = Color(0xFF101010)
    val SurfaceVariant = Color(0xFF15130F)
    val SurfaceElevated = Color(0xFF1D1A14)

    val CardBackground = Color(0xFF12110F)
    val CardBorder = Color(0xFF30291E)

    val Primary = Color(0xFFD6B56D)
    val PrimaryLight = Color(0xFFE8D5A8)
    val PrimaryDark = Color(0xFFA88950)
    val Secondary = Color(0xFFB58A45)
    val Accent = Color(0xFFC5A66B)

    val ButtonBackground = Color(0xFFD6B56D)
    val ButtonText = Color(0xFF100E09)

    val InputBackground = Color(0xFF101010)
    val InputBorder = Color(0xFF51432A)

    val NavbarBackground = Color(0xFF080808)
    val NavbarSelected = Color(0xFFD6B56D)
    val NavbarUnselected = Color(0xFF786F60)

    val Border = Color(0xFF393124)
    val Divider = Color(0xFF292319)

    val TextPrimary = Color(0xFFF2EBDD)
    val TextSecondary = Color(0xFFA69B88)
    val TextMuted = Color(0xFF786F60)

    val Success = Color(0xFFB5A078)
    val Warning = Color(0xFFE0B65C)
    val Danger = Color(0xFFD95B55)
    val Info = Color(0xFF9CABB8)

    // Semantic aliases
    val Stroke = Border
    val Emerald = Primary
    val EmeraldDark = PrimaryDark
    val SkyInfo = Info
    val AmberWarn = Warning
}

object WaShapes {
    val ButtonRadius = 12.dp
    val CardRadius = 14.dp
    val InputRadius = 10.dp
    val BorderWidth = 1.dp
}

private val ObsidianImperialColorScheme = darkColorScheme(
    primary = WaColors.Primary,
    onPrimary = WaColors.ButtonText,
    primaryContainer = WaColors.SurfaceElevated,
    onPrimaryContainer = WaColors.PrimaryLight,
    secondary = WaColors.Secondary,
    onSecondary = WaColors.ButtonText,
    secondaryContainer = WaColors.SurfaceVariant,
    onSecondaryContainer = WaColors.TextPrimary,
    tertiary = WaColors.Accent,
    onTertiary = WaColors.ButtonText,
    background = WaColors.Background,
    onBackground = WaColors.TextPrimary,
    surface = WaColors.Surface,
    onSurface = WaColors.TextPrimary,
    surfaceVariant = WaColors.SurfaceVariant,
    onSurfaceVariant = WaColors.TextSecondary,
    outline = WaColors.Border,
    outlineVariant = WaColors.Divider,
    error = WaColors.Danger,
    onError = WaColors.TextPrimary,
)

private val ImperialShapes = Shapes(
    small = RoundedCornerShape(WaShapes.InputRadius),
    medium = RoundedCornerShape(WaShapes.CardRadius),
    large = RoundedCornerShape(18.dp),
)

private val WaTypography = Typography(
    headlineMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
        letterSpacing = (-0.2).sp,
    ),
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 18.sp,
        lineHeight = 24.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 15.sp,
        lineHeight = 20.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 18.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.Normal,
        fontSize = 12.sp,
        lineHeight = 16.sp,
        fontFeatureSettings = "tnum",
    ),
)

@Composable
fun WaPowerTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = ObsidianImperialColorScheme,
        shapes = ImperialShapes,
        typography = WaTypography,
        content = content,
    )
}
