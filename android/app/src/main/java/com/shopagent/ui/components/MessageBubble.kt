package com.shopagent.ui.components

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import com.halilibo.richtext.commonmark.Markdown
import com.halilibo.richtext.ui.material3.RichText
import com.shopagent.domain.ChatMessage
import com.shopagent.domain.MessageStatus

@Composable
fun MessageBubble(
    message: ChatMessage,
    onRetry: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    when (message) {
        is ChatMessage.User -> UserBubble(message, modifier)
        is ChatMessage.Assistant -> AssistantBubble(message, onRetry, modifier)
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun UserBubble(
    message: ChatMessage.User,
    modifier: Modifier = Modifier,
) {
    val clipboardManager = LocalClipboardManager.current
    var menuExpanded by remember { mutableStateOf(false) }

    Box(modifier = modifier.fillMaxWidth()) {
        // 外层 Box 作为 DropdownMenu 的锚点，长按气泡弹出操作菜单
        Box(
            modifier = Modifier
                .align(Alignment.CenterEnd)
                .widthIn(max = 300.dp),
        ) {
            Surface(
                color = MaterialTheme.colorScheme.primary,
                shape = RoundedCornerShape(16.dp, 16.dp, 4.dp, 16.dp),
                modifier = Modifier.combinedClickable(
                    onClick = {},
                    onLongClick = { menuExpanded = true },
                ),
            ) {
                Text(
                    text = message.text,
                    color = MaterialTheme.colorScheme.onPrimary,
                    style = MaterialTheme.typography.bodyLarge,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                )
            }
            DropdownMenu(
                expanded = menuExpanded,
                onDismissRequest = { menuExpanded = false },
            ) {
                DropdownMenuItem(
                    text = { Text("复制") },
                    onClick = {
                        clipboardManager.setText(AnnotatedString(message.text))
                        menuExpanded = false
                    },
                )
            }
        }
    }
}

@Composable
private fun AssistantBubble(
    message: ChatMessage.Assistant,
    onRetry: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        verticalArrangement = Arrangement.spacedBy(8.dp),
        modifier = modifier.fillMaxWidth(),
    ) {
        if (message.products.isNotEmpty()) {
            ProductCardRow(products = message.products)
        }

        if (message.text.isNotEmpty()) {
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(16.dp, 16.dp, 16.dp, 4.dp),
            ) {
                // 助手回复为 Markdown（列表/加粗），流式增量重组时重新解析
                RichText(
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                ) {
                    Markdown(content = message.text)
                }
            }
        } else if (message.status == MessageStatus.Streaming) {
            // 回复尚未产出内容时，用仿豆包的三点跳动气泡占位
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(16.dp, 16.dp, 16.dp, 4.dp),
            ) {
                TypingIndicator(modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp))
            }
        }

        when (message.status) {
            // 已有部分内容仍在流式输出时，文本下方继续显示三点动画
            MessageStatus.Streaming -> if (message.text.isNotEmpty()) {
                TypingIndicator(modifier = Modifier.padding(start = 4.dp))
            }
            MessageStatus.Partial -> Text(
                text = "回复不完整",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.tertiary,
            )
            MessageStatus.Failed -> Text(
                text = "回复失败",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
            )
            MessageStatus.Done -> Unit
        }

        message.error?.let { error ->
            Surface(
                color = MaterialTheme.colorScheme.errorContainer,
                shape = RoundedCornerShape(8.dp),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                ) {
                    Text(
                        text = "${error.code}: ${error.message}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        modifier = Modifier.weight(1f),
                    )
                    if (error.retryable) {
                        Button(
                            onClick = { onRetry(message.id) },
                            modifier = Modifier.padding(start = 8.dp),
                        ) {
                            Text("重试")
                        }
                    }
                }
            }
        }
    }
}
