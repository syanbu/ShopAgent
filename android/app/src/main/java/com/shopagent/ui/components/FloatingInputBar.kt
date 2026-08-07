package com.shopagent.ui.components

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp

/**
 * 悬浮输入胶囊：无边框、阴影浮起，发送按钮内置于右侧。
 * 状态（输入文本）自持，发送后清空。
 */
@Composable
fun FloatingInputBar(
    isStreaming: Boolean,
    onSend: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var input by remember { mutableStateOf("") }
    val canSend = input.isNotBlank() && !isStreaming

    Surface(
        shape = RoundedCornerShape(28.dp),
        shadowElevation = 6.dp,
        color = MaterialTheme.colorScheme.surface,
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextField(
                value = input,
                onValueChange = { input = it },
                maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Default),
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = Color.Transparent,
                    unfocusedContainerColor = Color.Transparent,
                    disabledContainerColor = Color.Transparent,
                    focusedIndicatorColor = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                    disabledIndicatorColor = Color.Transparent,
                ),
                modifier = Modifier.weight(1f),
            )
            FilledIconButton(
                onClick = {
                    onSend(input)
                    input = ""
                },
                enabled = canSend,
                colors = IconButtonDefaults.filledIconButtonColors(
                    disabledContainerColor = MaterialTheme.colorScheme.surfaceVariant,
                    disabledContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                ),
                modifier = Modifier
                    .padding(end = 6.dp)
                    .size(40.dp),
            ) {
                Icon(
                    Icons.AutoMirrored.Filled.Send,
                    contentDescription = "发送",
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}
