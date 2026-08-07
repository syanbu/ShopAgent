package com.shopagent.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// 仿豆包配色：用户气泡用高饱和蓝，助手气泡用浅灰，页面白底
private val DoubaoBlue = Color(0xFF2B6CF2)
private val DoubaoBlueDark = Color(0xFF6B97FF)
private val AssistantBubbleGray = Color(0xFFF2F3F5)

private val LightColors = lightColorScheme(
    primary = DoubaoBlue,
    onPrimary = Color.White,
    surfaceVariant = AssistantBubbleGray,
)

private val DarkColors = darkColorScheme(
    primary = DoubaoBlueDark,
)

@Composable
fun ShopAgentTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        content = content,
    )
}
